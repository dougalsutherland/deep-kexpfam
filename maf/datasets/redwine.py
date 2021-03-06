import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

from . import *
import util
from Datasets import RedWine

class REDWINE:

    class Data:

        def __init__(self, data):

            self.x = data.astype(np.float32)
            self.N = self.x.shape[0]

    def __init__(self, noise_std=0.0, seed=1, **kwargs):

        dist = RedWine(noise_std=noise_std, ntest=500, seed=seed, **kwargs)
        trn, val, tst, idx = dist.data, dist.valid_data, dist.test_data, dist.idx

        self.trn = self.Data(trn)
        self.val = self.Data(val)
        self.tst = self.Data(tst)
        self.seed= seed
        self.idx = idx

        self.n_dims = self.trn.x.shape[1]

    def show_histograms(self, split):

        data_split = getattr(self, split, None)
        if data_split is None:
            raise ValueError('Invalid data split')

        util.plot_hist_marginals(data_split.x)
        plt.show()
