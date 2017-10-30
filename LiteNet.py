import tensorflow as tf
import numpy as np
from collections import OrderedDict
import operator
import itertools
import time

# =====================            
# Kernel related
# =====================            

class KernelNetModel:
    ''' A class that combines an ordinary kernel on the features
        extracted by a net

        This is used to build an infinite dimensional exp-fam model
        on the data, fitted using score matching

        There are two components: 
            kernel, responsible for computing the gram matrix
            network, responsible for network forward and derivatives

        This object then computes the derivatives of the function defined by the kernels
        and points that set up this function dk(x_i, y)/dy, which is then used to compute the score

    ''' 
    
    def __init__(self, kernel, network, alpha, points = None):

        ''' npoint: number of data that define the RKHS function
            ndim  : dimentionality of input kernel, an integer
            ndim_in: shape of input data to the network, a tuple
            X     : points that define the RKHS function
        '''
        
        self.alpha   = alpha
        self.npoint = alpha.shape[0].value
        self.ndim   = network.ndim_out[0]
        self.ndim_in= network.ndim_in
        self.network = network
        self.kernel  = kernel
        if points is not None:
            self.set_points(points)

    def _process_data(self, data):

        Y = self.network.forward_tensor(data)
        return Y


    def set_points(self, points):
        ''' This sets up the set of points used by model x_i's
            Input is a set of images that will first be processed by network
            These are stored inside the model as parameters
        '''
        assert points.shape[1:] == self.ndim_in, 'input shape of points not the same as predefiend'
        self.X = self.network.forward_tensor(points)

    def evaluate_kernel_fun(self, Y):
        '''
        takes in input vector Y (output of the network) of shape (ninput x ndim) 
        and return the function defined by the lite model, linear combination of kernel
        functions 
        
        sum_m alpha_m * k(x_m, y_i)

        this is used mainly for testing the gradients using autodiff
        
        '''
        if Y.shape.ndims == 1:
            Y = tf.expand_dims(Y,0)
        
        gram = self.kernel.get_gram_matrix(self.X, Y)

        return tf.einsum('i,ij', self.alpha, gram)
        
    def get_kernel_fun_grad(self, Y):

        ''' first derivative of the function of lite model '''

        K = self.kernel.get_grad(self.X, Y)
        grad = tf.reduce_sum(self.alpha[:,None, None] * K, 0)

        return grad, K
    
    def get_kernel_fun_hess(self, Y):
        '''
        gram matrix, multiplied by ( outer products of x_m - y_i, minus
                                     identity matrix of shape ndim x ndim)
        '''
        K = self.kernel.get_hess(self.X, Y)
        hess  = tf.reduce_sum(self.alpha[:,None, None, None] 
                                * K, 0)

        return hess, K

    def get_kernel_fun_grad_hess(self, Y):
        '''
        compute the gradient and hessian using one computation of gram matrix
        '''
        K1, K2 = self.kernel.get_grad_hess(self.X, Y)

        grad = tf.reduce_sum(self.alpha[:,None, None] * K1, 0)
        hess = tf.reduce_sum(self.alpha[:,None, None, None] * K2, 0)

        return grad, hess

    def _construct_index(self):
        ''' construct string for use in tf.einsum as it does not support...'''
        return ''.join([str(unichr(i+115)) for i in range(len(self.ndim_in))])

    def score(self, lam=0.0):

        points = tf.placeholder('float32', shape=(self.npoint,) + self.ndim_in)

        self.X = self._process_data(points)

        d2ydx2, dydx, y, data = self.network.get_sec_data()
        dfdy, d2fdy2   = self.get_kernel_fun_grad_hess(y)

        input_idx = self._construct_index()

        dfdx = tf.einsum('jk,kj'+input_idx+'->j'+input_idx,
                        dfdy, dydx)

        d2fdx2 = tf.einsum('jkl,kj'+input_idx + ',lj' + input_idx + '->j'+input_idx, 
                            d2fdy2, dydx, dydx)+\
                 tf.einsum('jk,kj'+input_idx + '->j'+input_idx, 
                            dfdy, d2ydx2)

        score = tf.reduce_sum(d2fdx2 + 0.5 * dfdx**2)
        score += lam * self.network.get_param_size()
        return score, points, data


    def linear_score(self, lam=0.0):

        '''
        The score that is applied to linear-ReLU networks such that
        the second derivatives w.r.t input are zeros
        '''

        points = tf.placeholder('float32', shape=(self.npoint,) + self.ndim_in)
        self.set_points(points)
        dydx, y, data = self.network.get_grad_data()
        dfdy, d2fdy2  = self.get_kernel_fun_grad_hess(y)

        input_idx = self._construct_index()

        dfdx = tf.einsum('jk,kj'+input_idx+'->j'+input_idx,
                        dfdy, dydx)
        
        d2fdx2 = tf.einsum('jkl,kj'+input_idx + ',lj' + input_idx + '->j'+input_idx, 
                            d2fdy2, dydx, dydx)

        score = tf.reduce_sum(d2fdx2 + 0.5 * dfdx**2)
        score += lam * self.network.get_param_size()
        return score, points, data

    def get_opt_alpha(self):
        ''' 
        This is used to get the optimal alpha, not tested
        '''
                                    
        points = tf.placeholder('float32', shape=(self.npoint,) + self.ndim_in)
        self.set_points(self._process_data(points))
        d2ydx2, dydx, y, data = self.network.get_sec_data()
        gradK = self.kernel.get_grad(y)
        hessK = self.kernel.get_hess(y)

        input_idx = self._construct_index()

        dkdx = tf.einsum('ijk,kj'+input_idx+'->ij'+input_idx,
                        gradK, dydx)

        d2kdx2 = tf.einsum('ijkl,klj'+input_idx + '->ij'+input_idx, 
                            hessK, dydx[None,...] * dydx[:,None,...]) + \
                 tf.einsum('ijk,kj'+input_idx + '->ij'+input_idx, 
                            gradK, d2ydx2)

        H = tf.reduce_sum(tf.reduce_sum(d2kdx2,-1), -1)
        G = tf.einsum('ik'+input_idx+',jk'+input_idx+'->ij',
                      dkdx, dkdx)

        alpha = tf.matrix_solve(G+tf.eye(G.shape[0].value)*0.1, -H[:,None])[:,0]
        opt_score = tf.einsum('i,i->', alpha, H) + \
                    0.5 * tf.einsum('i,ij,j', alpha, G, alpha)

        return alpha, opt_score, points, data
        
    ''' The following are methdos that compute the score of a model
        using an ordinary kernel only, and commpute
        the optimal coefficient of the kernel points[
            Gradient-free Hamiltonian Monte Carlo with Efficient Kernel Exponential Families
            Heiko Strathmann, Dino Sejdinovic, Samuel Livingstone, Zoltan Szabo, Arthur Gretton
            ]
    '''

    def score_original(self, data):

        ''' This computs the score objective without the network-dependent 
            base measure q_0
        '''

        sigma = self.kernel.sigma*1
        N = data.shape[0].value
        alpha = self.alpha
        ndim  = self.ndim

        # output of the network, same as x's
        Y = self.network.forward_tensor(data)

        # compute gram matrix of network output with points
        K = self.kernel.get_gram_matrix(self.X, Y)
        D = 1.0/sigma*self.kernel.pdist2 - ndim
        J = tf.reduce_sum(tf.expand_dims(alpha,1) * K * D) * 1.0 / N / sigma
        
        # X_il
        # Y_jl
        X = self.X
        # C = (X[:,None,:] - x[None,:,:]), C_ijl
        S = tf.expand_dims(X,1) - tf.expand_dims(Y,0)

        # alpha_exp = alpha[:,None, NOne], alpha_exp_i..
        alpha_exp = tf.expand_dims(tf.expand_dims(alpha, -1),-1)

        # K_exp = K[:,:,None], K_ij.
        K_exp     = tf.expand_dims(K, -1)

        J += 0.5/N/(sigma**2)*tf.reduce_sum(
                            tf.reduce_sum(alpha_exp * (S) * K_exp, 0
                        )**2)

        return J


    def _kernel_score_stats(self, Y):
        
        ''' compute the vector b and matrix C
            Y: the input data to the lite model to fit
        '''

        X = self.X
        sigma = self.kernel.sigma
        N = Y.shape[0]
        D = self.ndim

        K= self.kernel.get_gram_matrix(X, Y)
        K_exp = tf.expand_dims(K, -1)

        S = tf.expand_dims(X,1) - tf.expand_dims(Y,0)

        b =  -D * tf.reduce_sum(K,1)
        b += 1/sigma * tf.reduce_sum( K * tf.reduce_sum(S**2, 2), 1)

        # term inside the square that multiplies alpha_i
        A   = S * K_exp
        SA  = tf.einsum('ikl,jkl->ij', A, A)
        C   = (SA)

        return b, C

    def kernel_score(self, data=None):
        
        Y = self._process_data(data)
        ndata = data.shape.dims[0].value

        alpha = self.alpha
        alpha_V = tf.expand_dims(alpha,1)
        alpha_T = tf.expand_dims(alpha,0)

        sigma = self.kernel.sigma

        b, C = self._kernel_score_stats(Y)

        J = 1.0 / ndata / sigma    * tf.einsum('i,i'   , alpha, b) + \
            0.5 / ndata / sigma**2 * tf.einsum('i,ij,j', alpha, C, alpha)

        return J

    def kernel_fit(self, data=None, lam = 0.01):

        npoint = self.npoint
        Y = self._process_data(data)
        b, C = self._kernel_score_stats(Y)
        print C.eval()
       
        alpha_hat =  -self.kernel.sigma * \
                        tf.matrix_solve( C, tf.expand_dims(b,-1) )
        self.alpha = tf.assign(self.alpha, tf.squeeze(alpha_hat))
      
        return self.alpha



class KernelNetMSD:

    ''' 
        A class that combines an ordinary kernel and network that finds the 
        maximum Stein discrepency (MSD) of data under a particular parametric (probablistic)
        model, potentially unnormalized, whose gradients on the data (score) can be computed 

        This gradient is required to calculate MSD
    '''

    def __init__(self, kernel, network):
        
        self.batch_size = network.batch_size
        self.ndim       = network.ndim_out[0]
        self.ndim_in    = network.ndim_in
        self.network    = network
        assert self.ndim == self.network.ndim_out[0]
        self.kernel  = kernel
        self.points  = None

    def _construct_index(self):
        ''' construct string for use in tf.einsum as it does not support...'''
        return ''.join([str(unichr(i+115)) for i in range(len(self.ndim_in))])

    def MSD_V(self):

        dp_dx = tf.placeholder('float32', shape = (self.batch_size,) + self.ndim_in)
        dp_dy = tf.placeholder('float32', shape = (self.batch_size,) + self.ndim_in)

        dZX_dX, ZX, X = self.network.get_grad_data()
        dZY_dY, ZY, Y = self.network.get_grad_data()

        dk_dZX, dk_dZY, d2k_dZXdZY, gram  = self.kernel.get_two_grad_cross_hess(ZX, ZY)
        
        input_idx = self._construct_index()

        dk_dX = tf.einsum('ijk,ki'+input_idx+'->ij'+input_idx,
                        dk_dZX, dZX_dX)
        dk_dY = tf.einsum('ijk,kj'+input_idx+'->ij'+input_idx,
                        dk_dZY, dZY_dY)
        d2k_dXdY = tf.einsum('ijkl,ki' + input_idx + ',lj'+input_idx + '->ij'+input_idx, d2k_dZXdZY, dZX_dX, dZY_dY)
        h = tf.einsum('i'+input_idx +  ',j'+input_idx + '->ij', dp_dx, dp_dy) * gram + \
            tf.einsum('j'+input_idx + ',ij'+input_idx + '->ij', dp_dy, dk_dX) + \
            tf.einsum('i'+input_idx + ',ij'+input_idx + '->ij', dp_dx, dk_dY) + \
            tf.reduce_sum(d2k_dXdY, range(2,len(self.ndim_in)+2))

        h = tf.reduce_sum(h) / self.batch_size **2
        return h, X, Y, dp_dx, dp_dy

    def FSSD(self, points):

        dp_dx = tf.placeholder('float32', shape = (self.batch_size,) + self.ndim_in)
        dp_dy = tf.placeholder('float32', shape = (self.batch_size,) + self.ndim_in)

        dZX_dX, ZX, X = self.network.get_grad_data()
        dZY_dY, ZY, Y = self.network.get_grad_data()

        dk_dZX, gram  = self.kernel.get_grad_gram(ZX, points)
        dk_dZY, gram  = self.kernel.get_grad_gram(ZY, points)
        
        input_idx = self._construct_index()

        dk_dX = tf.einsum('ijk,ki'+input_idx+'->ij'+input_idx,
                        dk_dZX, dZX_dX)
        dk_dY = tf.einsum('ijk,kj'+input_idx+'->ij'+input_idx,
                        dk_dZY, dZY_dY)
        d2k_dXdY = tf.einsum('ijkl,ki' + input_idx + ',lj'+input_idx + '->ij'+input_idx, d2k_dZXdZY, dZX_dX, dZY_dY)
        h = tf.einsum('i'+input_idx +  ',j'+input_idx + '->ij', dp_dx, dp_dy) * gram + \
            tf.einsum('j'+input_idx + ',ij'+input_idx + '->ij', dp_dy, dk_dX) + \
            tf.einsum('i'+input_idx + ',ij'+input_idx + '->ij', dp_dx, dk_dY) + \
            tf.reduce_sum(d2k_dXdY, range(2,len(self.ndim_in)+2))

        h = tf.reduce_sum(h) / self.batch_size **2
        return h, X, Y, dp_dx, dp_dy







class GaussianKernel:

    '''
    Output and derivatives of Gaussain kernels
    X: the data points that define the function, rank 2
    Y: input data, rank 2
    '''

    def __init__(self, ndim, sigma = 1.0, X = None, npoint = None):

        '''
        X: points that define the kernel, X.shape = (npoint, ndim)
        '''
            
        self.sigma = sigma
        self.pdist2 = None
        self.grad   = None
        self.hess   = None


    def get_pdist2(self, X, Y):

        
        if X.shape.ndims==1:
            X = X[None,:]
        if Y.shape.ndims==1:
            Y = Y[None,:]
        assert X.shape[1] == Y.shape[1]
        pdist2 = tf.reduce_sum(X**2, axis=1, keep_dims=True)
        pdist2 -= 2.0*tf.matmul(X, Y, transpose_b = True)
        pdist2 += tf.matrix_transpose(tf.reduce_sum(Y**2, axis=1, keep_dims=True))
        self.pdist2 = pdist2
        return pdist2

    def get_gram_matrix(self, X, Y):

        pdist2 = self.get_pdist2(X, Y)
        sigma = self.sigma
        gram = tf.exp(-0.5/sigma*pdist2)
        return gram


    def get_grad(self, X, Y):
        ''' first derivative of the kernel on the second input, dk(x, y)/dy'''

        K, _ = self.get_grad_gram(X, Y)

        return K

    def get_grad_gram(self, X, Y):

        ''' first deriavtives and gram matrix, used for FSSD'''

        gram = self.get_gram_matrix(X, Y)[:,:,None]

        # D contrains the vector difference between pairs of x_m and y_i
        # divided by sigma
        D = (tf.expand_dims(X, 1) - tf.expand_dims(Y, 0))/self.sigma

        # K is a vector that has derivatives on all points
        K = (gram * D)

        return K, gram
        
    def get_hess(self, X, Y):

        ''' 
            first derivative of the kernel on the second input, d2k(x, y)/dy2
        '''
        gram = self.get_gram_matrix(X, Y)[:,:,None, None]
        # the first term
        D = ( tf.expand_dims(X, 1) - tf.expand_dims(Y, 0) )/self.sigma
        D2 = tf.einsum('ijk,ijl->ijkl', D, D)
        I  = tf.eye( D.shape[-1].value )/self.sigma

        # K is a vector that has the hessian on all points
        K = gram * (D2 - I)

        return K

    def get_grad_hess(self, X, Y):

        '''
        compute the first and second derivatives using one computation of gram matrix 
        '''

        gram = self.get_gram_matrix(X, Y)

        # D contrains the vector difference between pairs of x_m and y_i
        # divided by sigma
        D = (tf.expand_dims(X, 1) - tf.expand_dims(Y, 0))/self.sigma

        # K is a vector that has derivatives on all points
        K1 = (gram[:,:,None]* D)
        
        D2 = tf.einsum('ijk,ijl->ijkl', D, D)
        I  = tf.eye( D.shape[-1].value )/self.sigma

        # K is a vector that has the hessian on all points
        K2 = gram[:,:,None,None] * (D2 - I)

        return K1, K2

    def get_two_grad_cross_hess(self, X, Y):
        '''
        compute the derivatives of d2k/dx_idy_j, used for MSD
        and also the derivatives w.r.t x and y
        '''

        gram = self.get_gram_matrix(X, Y)
        
        D = (tf.expand_dims(X, 1) - tf.expand_dims(Y, 0))/self.sigma
        # dk_dy
        K2 = (gram[:,:,None] * D)
        # dk_dx
        K1 = -K2

        D2 = tf.einsum('ijk,ijl->ijkl', D, D)
        I  = tf.eye( D.shape[-1].value )/self.sigma

        K3 = gram[:,:,None,None] * (I - D2)
        return K1, K2, K3, gram
        
# =====================            
# Network related
# =====================            
class SumOutputNet:
    
    def __init__(self, net):

        '''
        Container object that stores variables in a network that sums the output over input batch

        sum_output  :   [  sum_j y_0(x_j), sum_j y_1(x_j), sum_j y_2(x_j), .. ,
                        sum_j y_ndim_out(x_j) for j in batch]
                        Create dummy variable so that gradient can be computed. This has the same length
                        as the number of outputs.
                        It works because the following
                        Let the output be y_i(x_j), each data x_j is paired with parameter copy w_j,
                        The i'th value of this variable, denoted by sum_i, is then sum_j y_i(x_j)
                        Then d(sum_i) / d(w_j) = d(y_i(x_j))

        output      :   output of the data through the network without sum
        batch       :   tensorflow placeholder for input batch
        batch_split :   one symbol for each data
        param_copy  :   copy of param_dict that is going to be paired with each data 
        
        '''

        # parameters are duplicated (not copied in memory) to facilitate parallel gradient over input batch
        # it creates different symbols that points to the same parameters via the tf.identity function
        # each copy of the parameter will be paired with a single input data for forward computation
        '''
        self.param_copy = [OrderedDict(
                        zip( net.param.keys(), 
                             [tf.identity(v) for v in net.param.values()]
                           )
                    ) for _ in xrange(net.batch_size)]
        '''
        # create input placeholder that has batch_size
        self.batch = tf.placeholder(tf.float32, shape = (net.batch_size,) + net.ndim_in)
        # split it into different symbols to be paired with parameter copies
        self.batch_split = tf.split(self.batch, net.batch_size, axis = 0)

        # output for each data
        self.output = net.forward_tensor(self.batch) 

        # sum the output over batch sum_output[i] = sum_j y_i(x_j)
        self.sum_output = tf.reduce_sum(self.output, axis=0)

class Network:
    '''
    Network should be implemented as subclass of Network

    Define a forward_tensor method which takes in data as tf.Tensor (placeholder) of shape
        [ndata, ...], e.g. [ndata, C, H, W] for conv net

    and return the output as a vector
        [ndata, ndim_out]
    This class contains methods that computes gradients w.r.t. data and parameter (may not be required)
    and second derivative w.r.t. data
    
    '''
   

    # input shape should be a tuple
    ndim_in = None
    # output shape is a scalar. Output assumed to be 1-D
    ndim_out= None
    # how much data to process for gradients, only for gradients and second gradients now.
    batch_size   = None
    # network parameter as a dictionary
    param   = None
    # empty now...
    out     = None

    def __init__(self, ndim_in, ndim):

        raise NotImplementedError('should implement individual networks')

    def reshape_data_array(self, x):
        ''' check if input is 1-D and augment if so'''
        if x.ndim == len(self.ndim_in):
            if x.shape == self.ndim_in:
                x = x[None,:]
                single = True
            else:
                raise NameError('input dimension is wrong')
        else:
            if x.shape[1:] != self.ndim_in:
                raise NameError('input dimension is wrong')
            else:
                single = False
        return x, single

    def reshape_data_tensor(self, x):
        ''' check if input is 1-D and augment if so '''
        if x.shape.ndims == len(self.ndim_in):
            if x.shape == self.ndim_in:
                x = tf.expand_dims(x, 0)
                single = True
            else:
                raise NameError('input dimension is wrong')
        else:
            if x.shape[1:] != self.ndim_in:
                raise NameError('input dimension is wrong')
            else:
                single = False
        return x, single
        

    def forward_array(self, data):
        ''' Output of the network given data array as np.array '''
        raise NotImplementedError('should implement in individual networks')

    def forward_tensor(self, data):
        ''' Output of the network given data array as tf.Tensor '''
        raise NotImplementedError('should implement in individual networks')

    
    #def get_grad_param(self, data):

    #    ''' Compute the derivative of each output to all the parameters
    #        and store in dictionary, also return network output

    #        data:   numpy array
    #        return: grad_value_dict{param_name=>gradients of shape (ndim_out, ninput, param.shape)}
    #                network output of shape (ninput, ndim_out)
    #    '''

    #    # reshape data to see if there is a single input
    #    data, single   = self.reshape_data_array(data)
    #    ninput = data.shape[0]
    #    # number of batches
    #    nbatch = ninput/self.batch_size

    #    son = SumOutputNet(self)
    #    
    #    # rearrange the parameter handles into a single array so tf.gradients can consume. It is arrange like:
    #    #         [ w_1_copy1, w_1_copy_2, w_1_copy_3, ..., w_nparam_copy_batch_size ]
    #    batch_param = [ son.param_copy[i][k] for k in self.param.keys() for i in xrange(self.batch_size) ]

    #    # grad will be arranged by [ ndim_out [len(batch_param)] ]
    #    grad   = [ list(tf.gradients(son.sum_output[oi], batch_param)) for oi in xrange(self.ndim_out)]

    #    # results stores grad over multiple batches
    #    results = []
    #    sess = tf.Session()
    #    init = tf.global_variables_initializer()
    #    sess.run(init)

    #    for bi in xrange(nbatch):
    #        # get a batch
    #        batch_data = data[bi*self.batch_size:(bi+1)*self.batch_size]
    #        # run and append results, the first argument is [[w_1_copies], [w_2_copies], network output]
    #        results.append(sess.run(grad + [son.output], feed_dict = {son.batch : batch_data}))

    #    # create output dictionary what will be indexed by parameter name.
    #    # grad_value_dict[parameter_name][output_idx][input_idx][param_dims]
    #    grad_value_dict = OrderedDict.fromkeys(self.param.keys())
    #   
    #    # results are arranged as follows
    #    # results   [batch_idx  [  [output [param_1_copy_1, param_1_copy_2, ... param_nparam_copy_nbatch]],
    #    #                           networkoutput
    #    #                       ]
    #    #           ]
    #    # first fix a k, then form an array of [ndim_out x batch_size*nbatch]
    #    for ki, k in enumerate(self.param.keys()):
    #        grad_k = np.array([      
    #                    np.concatenate([
    #                        results[bi][oi][ki*self.batch_size : (ki+1)*self.batch_size ] for bi in xrange(nbatch)
    #                    ]) for oi in xrange(self.ndim_out)
    #                 ])
    #        grad_value_dict[k] = grad_k

    #    
    #    # extract the output by using the last index of each batch
    #    output_value = np.concatenate([results[bi][-1] for bi in xrange(nbatch)])
    #    return grad_value_dict, output_value

    def get_grad_data(self):

        ''' get first derivative with respect to data,
            return gradient node, output node and input (feed) node
        '''
        
        t0 = time.time()
        son = SumOutputNet(self)
        grad = []
        t0 = time.time()
        t1 = time.time()
        print 'building grad output'
        for oi in xrange(self.ndim_out[0]):
            g = tf.gradients(son.sum_output[oi], son.batch)[0]
            grad.append(g)
            print '\r%3d out of %3d took %5.3f sec' % ( oi, self.ndim_out[0], time.time()-t1),
            ti = time.time()
        grad   = tf.stack(grad)
        print 'building grad output took %.3f sec' % (time.time() - t0)

        return grad, son.output, son.batch

    def get_sec_data(self):

        ''' get second derivative node with respect to data,
            return second derivative node, gradient node, network output node and input (feed) node
        '''

        son = SumOutputNet(self)
        # sum over batch again
        grad = tf.stack([ tf.gradients(son.sum_output[oi], son.batch)[0] for oi in xrange(self.ndim_out[0])])

        sec = []

        sum_grad = tf.reduce_sum(grad, 1)
        g_flat = tf.reshape(sum_grad, [self.ndim_out[0], -1])

        # this is the linear index for the input dimensions
        raveled_index = np.arange(np.prod(self.ndim_in)).astype('int32')
        unraveled_index = np.array(np.unravel_index(raveled_index, self.ndim_in))

        for oi in xrange(self.ndim_out[0]):
            t0 = time.time()
            print 'building output %d out of %d' % (oi+1, self.ndim_out[0])

            # tf.gather_nd(A, idx) takes tensor A and return elements whose indices are specified in idx
            # like [A[i] for i in idx] 
            # The following line takes derivative of each the gradient at oi'th output dimension
            # w.r.t. each input dimension, stack over input dimension
            #   idx looks like:
            #   [   [0, input_dim_1, input_dim_2, ... ,],
            #       [1, input_dim_1, input_dim_2, ... ,],
            #       ...
            #       [batch_size, input_dim_1, input_dim_2, ... ,]
            #   ]
            # And the result below is of shape [prod(self.ndim_in), batch_size]
            sec_oi_ii = tf.stack([ tf.gather_nd(
                                        self._grad_zero(g_flat[oi, i], [son.batch])[0],
                                        np.c_[  np.arange(self.batch_size)[:,None], 
                                                np.tile(unraveled_index[:,i],[self.batch_size,1])]
                                        )
                                    for i in raveled_index])
            print 'took %.3f sec' % (time.time()-t0)
            sec.append(sec_oi_ii)
        sec_stack = tf.stack(sec)
        # make the output shape [ ndim_out, batch_size, prod(self.ndim_in) ]
        sec_stack = tf.transpose(sec_stack, perm=[0,2,1])
        sec_stack = tf.reshape(sec_stack, (self.ndim_out[0], self.batch_size,) +  self.ndim_in)
        return sec_stack, grad, son.output, son.batch

    @staticmethod
    def _grad_zero(f, x):
        # x has to be a list
        grad = tf.gradients(f, x)
        for gi, g in enumerate(grad):
            if g is None:
                grad[gi] = tf.zeros_like(x[gi])
        return grad 

    def get_param_size(self):
        
        size = 0.0
        for _, v in self.param.items():
            size += tf.reduce_sum(v**2.0) 
        return size 

class LinearNetwork(Network):

    ''' y =  W \cdot x + b '''

    def __init__(self, ndim_in, ndim_out, batch_size = 2, init_std = 1.0, init_mean = 0.0):
        
        self.ndim_out  = ndim_out
        self.ndim_in = ndim_in
        self.batch_size = batch_size
        W   = tf.Variable(init_mean + np.random.randn(ndim_out[0], *ndim_in).astype('float32'))*init_std
        b   = tf.Variable(np.random.randn(1, ndim_out[0]).astype('float32'))*init_std
        self.param = OrderedDict([('W', W), ('b', b)])

    def forward_array(self, data, param = None):
        
        if param is None:
            param = self.param
        data, single = self.reshape_data_array(data)
            
        data_tensor  = tf.placeholder('float32', shape= (None, ) + self.ndim_in)

        W = param['W']
        b = param['b']
        out = tf.matmul(data_tensor, W,  transpose_b = True)
        out += b
        with tf.Session() as sess:
            init = tf.global_variables_initializer()
            sess.run(init)
            out_value = sess.run(out, feed_dict={data_tensor: data})
        if single:
            out_value = out_value[0]
        return out_value

    def forward_tensor(self, data, param = None):
        
        if param is None:
            param = self.param
        data, single = self.reshape_data_tensor(data)
            
        W = param['W']
        b = param['b']
        out = tf.matmul(data, W,  transpose_b = True)

        out += b

        if single:
            out = out[0]
        return out

    def get_grad_data(self):

        param = self.param

        # create input placeholder that has batch_size
        data = tf.placeholder(tf.float32, shape = (self.batch_size,) + self.ndim_in)
        data, single = self.reshape_data_tensor(data)
        W = param['W']
        output = self.forward_tensor(data, param)
        grad = tf.tile(W[None,:,:], [self.batch_size, 1, 1])
        grad = tf.transpose(grad, [1,0,2])
        return grad, output, data

class SquareNetwork(Network):

    ''' y =  ( W \cdot x + b ) ** 2 '''

    def __init__(self, ndim_in, ndim_out, batch_size = 2):
        
        self.ndim_out  = ndim_out
        self.ndim_in = ndim_in
        self.batch_size = batch_size
        W     = tf.Variable(np.random.randn(ndim_out[0], *ndim_in).astype('float32'))
        b      = tf.Variable(np.random.randn(1, ndim_out[0]).astype('float32'))
        self.param = OrderedDict([('W', W), ('b', b)])

    def forward_array(self, data, param = None):
        
        if param is None:
            param = self.param
        data, single = self.reshape_data_array(data)
            
        data_tensor  = tf.placeholder('float32', shape= (None, ) + self.ndim_in)

        W = param['W']
        b = param['b']
        out = tf.matmul(data_tensor, W,  transpose_b = True)
        out += b
        out = out ** 2.0
        with tf.Session() as sess:
            init = tf.global_variables_initializer()
            sess.run(init)
            out_value = sess.run(out, feed_dict={data_tensor: data})
        if single:
            out_value = out_value[0]
        return out_value

    def forward_tensor(self, data, param = None):
        
        if param is None:
            param = self.param
        data, single = self.reshape_data_tensor(data)
            
        W = param['W']
        b = param['b']
        out = tf.matmul(data, W,  transpose_b = True)
        out += b
        out = out ** 2.0

        if single:
            out = out[0]
        return out

class LinearReLUNetwork(Network):

    ''' y =  ReLU( W \cdot x + b ) '''

    def __init__(self, ndim_in, ndim_out, batch_size = 2, 
                init_std = 1.0, init_mean = 0.0, 
                lin_grads = [0.0, 1.0]):
        
        self.ndim_out  = ndim_out
        self.ndim_in = ndim_in
        self.batch_size = batch_size
        W   = tf.Variable((init_mean + init_std*np.random.randn(ndim_out[0], *ndim_in)).astype('float32'))
        b   = tf.Variable(np.zeros((1, ndim_out[0])).astype('float32'))
        self.lin_grads = lin_grads
        self.param = OrderedDict([('W', W), ('b', b)])

    def forward_array(self, data, param = None):
        
        if param is None:
            param = self.param
        data, single = self.reshape_data_array(data)
            
        data_tensor  = tf.placeholder('float32', shape= (None, ) + self.ndim_in)

        W = param['W']
        b = param['b']
        out = tf.matmul(data_tensor, W,  transpose_b = True)
        out += b
        out = tf.maximum(out*(self.lin_grads[1]), out*(self.lin_grads[0]))
        with tf.Session() as sess:
            init = tf.global_variables_initializer()
            sess.run(init)
            out_value = sess.run(out, feed_dict={data_tensor: data})
        if single:
            out_value = out_value[0]
        return out_value

    def forward_tensor(self, data, param = None):
        
        if param is None:
            param = self.param
        data, single = self.reshape_data_tensor(data)
            
        W = param['W']
        b = param['b']
        out = tf.matmul(data, W,  transpose_b = True)
        out += b
        out = tf.maximum(out * self.lin_grads[1], out * self.lin_grads[0])

        if single:
            out = out[0]
        return out
   
    def get_grad_data(self):


        # create input placeholder that has batch_size
        data = tf.placeholder(tf.float32, shape = (self.batch_size,) + self.ndim_in)
        data, _ = self.reshape_data_tensor(data)
        out =   self.forward_tensor(data, self.param)
        grad = self.get_grad_input(data, out)

        grad = tf.transpose(grad, [1,0,2])
        return grad, out, data

    def get_grad_input(self, i, o):

        # create input placeholder that has batch_size
        W = self.param['W']
        grad = (self.lin_grads[1] * tf.cast(o > 0, 'float32' ) + \
                self.lin_grads[0] * tf.cast(o <=0, 'float32' )) [:,:,None] * \
                W[None,:,:]
        return grad
  
class ConvNetwork(Network):

    ''' one layer convolution network'''

    def __init__(self, ndim_in, nfil, size, stride=1, batch_size = 2, flatten=False, 
                    init_std = 1.0, init_mean = 0.0, 
                    lin_grads=[0.0,1.0]):
        
        self.ndim_in = ndim_in
        self.batch_size = batch_size

        self.nfil    = nfil
        self.size    = size
        self.stride  = stride
        self.flatten = flatten
        self.lin_grads = lin_grads

        assert ndim_in[1] == ndim_in[2]
        
        self.size_out = (ndim_in[1]-size)/stride + 1
        if flatten:
            self.ndim_out = (( self.size_out ) **2 * nfil, )
        else:
            self.ndim_out = ( nfil, self.size_out, self.size_out )

        W      = tf.Variable((init_mean+np.random.randn( * ((self.size,self.size) + ndim_in[0:1] + (nfil,)) ) * init_std).astype('float32'))
        b      = tf.Variable((np.random.randn( * (1, nfil, self.size_out, self.size_out) ) * 0.0).astype('float32'))
        self.param = OrderedDict([('W', W), ('b', b)])
        
                                                        
    
    def forward_array(self, data, param = None):
        
        if param is None:
            param = self.param
        data, single = self.reshape_data_array(data)
        ndata = data.shape[0]
        data_tensor  = tf.placeholder('float32', shape= (ndata, ) + self.ndim_in)

        W = param['W']
        b = param['b']

        conv = tf.nn.conv2d(data_tensor, W,
                            [1,1]+[self.stride]*2, 'VALID', data_format='NCHW')
        conv = conv + b
        if self.flatten:
            conv = tf.reshape(conv,[ndata,-1])
        out = tf.maximum(conv * self.lin_grads[1], conv * self.lin_grads[0])

        with tf.Session() as sess:
            init = tf.global_variables_initializer()
            sess.run(init)
            out_value = sess.run(out, feed_dict={data_tensor: data})
        if single:
            out_value = out_value[0]
        return out_value

    def forward_tensor(self, data, param = None):
        
        if param is None:
            param = self.param
        data, single = self.reshape_data_tensor(data)
        ndata = data.shape[0].value
            
        W = param['W']
        b = param['b']

        conv = tf.nn.conv2d(data, W,
                            [1,1]+[self.stride]*2, 'VALID', data_format='NCHW')
        conv = conv + b
        out = tf.maximum(conv * self.lin_grads[1], conv * self.lin_grads[0])
        if self.flatten:
            out = tf.reshape(out,[ndata,-1])

        if single:
            out = out[0]
        return out
    
    ''' 
    def get_grad_data(self):

        assert self.flatten
        data = tf.placeholder(tf.float32, shape = (self.batch_size,) + self.ndim_in)
        data, _ = self.reshape_data_tensor(data)
        out  =   self.forward_tensor(data, self.param)
        grad = self.get_grad_input(data, out)

        grad = tf.transpose(grad, [1,0,2,3,4])
        return grad, out, data

    def get_grad_input(self, i, o):

        grad_temp = []
        for c in range(self.nfil):
            grad_temp_c = []
            for i in range(self.size_out):
                grad_temp_ci = []
                for j in range(self.size_out):
                    out_grad = np.zeros((1, self.nfil, self.size_out, self.size_out))
                    out_grad[0,c,i,j]=1.0
                    grad_temp_ci.append(tf.nn.conv2d_backprop_input( (1,)+self.ndim_in, 
                                                                        self.param['W'],
                                                                        out_grad,
                                                                        (1,1,)+(self.stride,)*2,
                                                                        'VALID',
                                                                        data_format='NCHW',
                                                                      )[0]
                                        )
                grad_temp_ci = tf.stack(grad_temp_ci)
                grad_temp_c.append(grad_temp_ci)

            grad_temp_c = tf.stack(grad_temp_c)
            grad_temp.append(grad_temp_c)
        
        self.grad_temp = tf.stack(grad_temp)
        if self.flatten:
            self.grad_temp = tf.reshape(self.grad_temp, (-1,) + self.ndim_in)

        # create input placeholder that has batch_size
        grad = (self.lin_grads[1] * tf.cast(o > 0, 'float32' ) + \
                self.lin_grads[0] * tf.cast(o <=0, 'float32' ))[...,None, None, None] * \
                self.grad_temp[None,...]

        return grad
    ''' 

class DeepNetwork(Network):
    

    def __init__(self, layers):
        
        self.nlayer = len(layers)
        self.layers = layers 
        self.ndim_out = layers[-1].ndim_out
        self.ndim_in  = layers[0].ndim_in
        self.batch_size = layers[0].batch_size
        self.param = OrderedDict()
        for li, l in enumerate(layers):
            for lp in l.param:
                self.param['layer'+str(li+1)+lp] = l.param[lp]
        self._check_consistency(layers)
    
    def _check_consistency(self, layers):

        nlayer = self.nlayer 

        for li, l in enumerate(layers):
            # check if all networks
            assert isinstance(l, Network)

            # check if input and output dimentions match
            if li < nlayer-1:
                if isinstance(l.ndim_out, tuple):
                    assert layers[li+1].ndim_in == l.ndim_out
                else:
                    assert layers[li+1].ndim_in == (l.ndim_out,)

            assert l.batch_size == self.batch_size

            
           
    def forward_tensor(self, data):
        
        assert isinstance(data, tf.Tensor)
        out = data
        for l in self.layers:
            out = l.forward_tensor(out)
        return out

    def forward_array(self, data, param=None):
        
        assert isinstance(data, np.ndarray)
        out = data
        for l in self.layers:
            out = l.forward_array(out)
        return out
    
    @staticmethod
    def _construct_index(shape, offset='o'):
        ''' construct string for use in tf.einsum as it does not support...'''
        return ''.join([str(unichr(i+ord(offset))) for i in range(len(shape))])
    '''
    def get_grad_data(self):
        
        data = tf.placeholder(tf.float32, shape = (self.batch_size,) + self.ndim_in)

        outs   = [None]*self.nlayer
        ins    = [None]*self.nlayer
        ins[0] = data
        out  = data
        for li, l in enumerate(self.layers):
            out = l.forward_tensor(out)
            if li < self.nlayer-1:
                ins[li+1] = out
            outs[li] = out

        grad = self.layers[-1].get_grad_input(ins[-1], outs[-1])
        for li, l in enumerate(self.layers[::-1]):

            if li == 0: continue

            li = self.nlayer-1-li

            this_grad = l.get_grad_input(ins[li], outs[li])
            output_idx = self._construct_index(l.ndim_out, 'k')
            input_idx  = self._construct_index(l.ndim_in, 'p')
            
            print 'gradients: ',grad, this_grad
            print 'ij'+output_idx+',i'+output_idx+input_idx+'->ij'+input_idx

            grad = tf.einsum('ij'+output_idx+',i'+output_idx+input_idx+'->ij'+input_idx
                            ,grad, this_grad)
            print grad
        
        grad = tf.transpose(grad,[1,0,]+range(2,2+len(self.ndim_in)))

        return grad, out, data
    '''
    

