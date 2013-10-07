# Copyright (c) 2012, 2013 Ricardo Andrade
# Licensed under the BSD 3-clause license (see LICENSE.txt)

import numpy as np
from scipy import stats,special
import scipy as sp
from GPy.util.univariate_Gaussian import std_norm_pdf,std_norm_cdf
import gp_transformations
from noise_distributions import NoiseDistribution

class Gaussian(NoiseDistribution):
    """
    Gaussian likelihood

    :param mean: mean value of the Gaussian distribution
    :param variance: mean value of the Gaussian distribution
    """
    def __init__(self,gp_link=None,analytical_mean=False,analytical_variance=False,variance=1., D=None, N=None):
        self.variance = variance
        self.D = D
        self.N = N
        self._set_params(np.asarray(variance))
        super(Gaussian, self).__init__(gp_link,analytical_mean,analytical_variance)

    def _get_params(self):
        return np.array([self.variance])

    def _get_param_names(self):
        return ['noise_model_variance']

    def _set_params(self, p):
        self.variance = float(p)
        self.I = np.eye(self.N)
        self.covariance_matrix = self.I * self.variance
        self.Ki = self.I*(1.0 / self.variance)
        #self.ln_det_K = np.sum(np.log(np.diag(self.covariance_matrix)))
        self.ln_det_K = self.N*np.log(self.variance)

    def _laplace_gradients(self, y, f, extra_data=None):
        #must be listed in same order as 'get_param_names'
        derivs = ([self.dlik_dvar(y, f, extra_data=extra_data)],
                  [self.dlik_df_dvar(y, f, extra_data=extra_data)],
                  [self.d2lik_d2f_dvar(y, f, extra_data=extra_data)]
                 ) # lists as we might learn many parameters
        # ensure we have gradients for every parameter we want to optimize
        assert len(derivs[0]) == len(self._get_param_names())
        assert len(derivs[1]) == len(self._get_param_names())
        assert len(derivs[2]) == len(self._get_param_names())
        return derivs

    def _gradients(self,partial):
        return np.zeros(1)
        #return np.sum(partial)

    def _preprocess_values(self,Y):
        """
        Check if the values of the observations correspond to the values
        assumed by the likelihood function.
        """
        return Y

    def _moments_match_analytical(self,data_i,tau_i,v_i):
        """
        Moments match of the marginal approximation in EP algorithm

        :param i: number of observation (int)
        :param tau_i: precision of the cavity distribution (float)
        :param v_i: mean/variance of the cavity distribution (float)
        """
        sigma2_hat = 1./(1./self.variance + tau_i)
        mu_hat = sigma2_hat*(data_i/self.variance + v_i)
        sum_var = self.variance + 1./tau_i
        Z_hat = 1./np.sqrt(2.*np.pi*sum_var)*np.exp(-.5*(data_i - v_i/tau_i)**2./sum_var)
        return Z_hat, mu_hat, sigma2_hat

    def _predictive_mean_analytical(self,mu,sigma):
        new_sigma2 = self.predictive_variance(mu,sigma)
        return new_sigma2*(mu/sigma**2 + self.gp_link.transf(mu)/self.variance)

    def _predictive_variance_analytical(self,mu,sigma,predictive_mean=None):
        return 1./(1./self.variance + 1./sigma**2)

    def _mass(self,gp,obs):
        #return std_norm_pdf( (self.gp_link.transf(gp)-obs)/np.sqrt(self.variance) )
        #Assumes no covariance, exp, sum, log for numerical stability
        return np.exp(np.sum(np.log(stats.norm.pdf(obs,self.gp_link.transf(gp),np.sqrt(self.variance)))))

    def _nlog_mass(self,gp,obs, extra_data=None):
        """
        Negative Log likelihood function

        .. math::
            \\-ln p(y_{i}|f_{i}) = +\\frac{D \\ln 2\\pi}{2} + \\frac{\\ln |K|}{2} + \\frac{(y_{i} - f_{i})^{T}\\sigma^{-2}(y_{i} - f_{i})}{2}

        :param y: data
        :type y: Nx1 array
        :param f: latent variables f
        :type f: Nx1 array
        :param extra_data: extra_data which is not used in student t distribution - not used
        :returns: likelihood evaluated for this point
        :rtype: float
        """
        assert gp.shape == obs.shape
        return .5*(np.sum((self.gp_link.transf(gp)-obs)**2/self.variance) + self.ln_det_K + self.N*np.log(2.*np.pi))

    def _dnlog_mass_dgp(self,gp,obs):
        return (self.gp_link.transf(gp)-obs)/self.variance * self.gp_link.dtransf_df(gp)

    def _d2nlog_mass_dgp2(self,gp,obs):
        return ((self.gp_link.transf(gp)-obs)*self.gp_link.d2transf_df2(gp) + self.gp_link.dtransf_df(gp)**2)/self.variance

    def _mean(self,gp):
        """
        Expected value of y under the Mass (or density) function p(y|f)

        .. math::
            E_{p(y|f)}[y]
        """
        return self.gp_link.transf(gp)

    def _dmean_dgp(self,gp):
        return self.gp_link.dtransf_df(gp)

    def _d2mean_dgp2(self,gp):
        return self.gp_link.d2transf_df2(gp)

    def _variance(self,gp):
        """
        Variance of y under the Mass (or density) function p(y|f)

        .. math::
            Var_{p(y|f)}[y]
        """
        return self.variance

    def _dvariance_dgp(self,gp):
        return 0

    def _d2variance_dgp2(self,gp):
        return 0

    def lik_function(self, y, f, extra_data=None):
        """
        Log likelihood function

        .. math::
            \\ln p(y_{i}|f_{i}) = -\\frac{D \\ln 2\\pi}{2} - \\frac{\\ln |K|}{2} - \\frac{(y_{i} - f_{i})^{T}\\sigma^{-2}(y_{i} - f_{i})}{2}

        :param y: data
        :type y: Nx1 array
        :param f: latent variables f
        :type f: Nx1 array
        :param extra_data: extra_data which is not used in student t distribution - not used
        :returns: likelihood evaluated for this point
        :rtype: float
        """
        assert y.shape == f.shape
        e = y - f
        objective = (- 0.5*self.N*np.log(2*np.pi)
                     - 0.5*self.ln_det_K
                     - (0.5/self.variance)*np.sum(np.square(e)) # As long as K is diagonal
                     )
        return np.sum(objective)

    def dlik_df(self, y, f, extra_data=None):
        """
        Gradient of the link function at y, given f w.r.t f

        .. math::
            \\frac{d \\ln p(y_{i}|f_{i})}{df} = \\frac{1}{\\sigma^{2}}(y_{i} - f_{i})

        :param y: data
        :type y: Nx1 array
        :param f: latent variables f
        :type f: Nx1 array
        :param extra_data: extra_data which is not used in student t distribution - not used
        :returns: gradient of likelihood evaluated at points
        :rtype: Nx1 array

        """
        assert y.shape == f.shape
        s2_i = (1.0/self.variance)
        grad = s2_i*y - s2_i*f
        return grad

    def d2lik_d2f(self, y, f, extra_data=None):
        """
        Hessian at y, given f, w.r.t f the hessian will be 0 unless i == j
        i.e. second derivative lik_function at y given f_{i} f_{j}  w.r.t f_{i} and f_{j}

        .. math::
            \\frac{d^{2} \\ln p(y_{i}|f_{i})}{d^{2}f} = -\\frac{1}{\\sigma^{2}}

        :param y: data
        :type y: Nx1 array
        :param f: latent variables f
        :type f: Nx1 array
        :param extra_data: extra_data which is not used in student t distribution - not used
        :returns: Diagonal of hessian matrix (second derivative of likelihood evaluated at points f)
        :rtype: Nx1 array

        .. Note::
            Will return diagonal of hessian, since every where else it is 0, as the likelihood factorizes over cases
            (the distribution for y_{i} depends only on f_{i} not on f_{j!=i}
        """
        assert y.shape == f.shape
        hess = -(1.0/self.variance)*np.ones((self.N, 1))
        return hess

    def d3lik_d3f(self, y, f, extra_data=None):
        """
        Third order derivative log-likelihood function at y given f w.r.t f

        .. math::
            \\frac{d^{3} \\ln p(y_{i}|f_{i})}{d^{3}f} = 0

        :param y: data
        :type y: Nx1 array
        :param f: latent variables f
        :type f: Nx1 array
        :param extra_data: extra_data which is not used in student t distribution - not used
        :returns: third derivative of likelihood evaluated at points f
        :rtype: Nx1 array
        """
        assert y.shape == f.shape
        d3lik_d3f = np.diagonal(0*self.I)[:, None]
        return d3lik_d3f

    def dlik_dvar(self, y, f, extra_data=None):
        """
        Gradient of the log-likelihood function at y given f, w.r.t variance parameter (noise_variance)

        .. math::
            \\frac{d \\ln p(y_{i}|f_{i})}{d\\sigma^{2}} = \\frac{N}{2\\sigma^{2}} + \\frac{(y_{i} - f_{i})^{2}}{2\\sigma^{4}}

        :param y: data
        :type y: Nx1 array
        :param f: latent variables f
        :type f: Nx1 array
        :param extra_data: extra_data which is not used in student t distribution - not used
        :returns: derivative of likelihood evaluated at points f w.r.t variance parameter
        :rtype: float
        """
        assert y.shape == f.shape
        e = y - f
        s_4 = 1.0/(self.variance**2)
        dlik_dsigma = -0.5*self.N/self.variance + 0.5*s_4*np.dot(e.T, e)
        return np.sum(dlik_dsigma) # Sure about this sum?

    def dlik_df_dvar(self, y, f, extra_data=None):
        """
        Derivative of the dlik_df w.r.t variance parameter (noise_variance)

        .. math::
            \\frac{d}{d\\sigma^{2}}(\\frac{d \\ln p(y_{i}|f_{i})}{df}) = \\frac{1}{\\sigma^{4}}(-y_{i} + f_{i})

        :param y: data
        :type y: Nx1 array
        :param f: latent variables f
        :type f: Nx1 array
        :param extra_data: extra_data which is not used in student t distribution - not used
        :returns: derivative of likelihood evaluated at points f w.r.t variance parameter
        :rtype: Nx1 array
        """
        assert y.shape == f.shape
        s_4 = 1.0/(self.variance**2)
        dlik_grad_dsigma = -np.dot(s_4*self.I, y) + np.dot(s_4*self.I, f)
        return dlik_grad_dsigma

    def d2lik_d2f_dvar(self, y, f, extra_data=None):
        """
        Gradient of the hessian (d2lik_d2f) w.r.t variance parameter (noise_variance)

        .. math::
            \\frac{d}{d\\sigma^{2}}(\\frac{d^{2} \\ln p(y_{i}|f_{i})}{d^{2}f}) = \\frac{1}{\\sigma^{4}}

        :param y: data
        :type y: Nx1 array
        :param f: latent variables f
        :type f: Nx1 array
        :param extra_data: extra_data which is not used in student t distribution - not used
        :returns: derivative of hessian evaluated at points f and f_j w.r.t variance parameter
        :rtype: Nx1 array
        """
        assert y.shape == f.shape
        dlik_hess_dsigma = np.diag((1.0/(self.variance**2))*self.I)[:, None]
        return dlik_hess_dsigma
