from . import LatentFunctionInference
from ...util import linalg
from ...util import choleskies
import numpy as np
from .posterior import Posterior

class SVGP(LatentFunctionInference):

    def inference(self, q_u_mean, q_u_chol, kern, X, Z, likelihood, Y, mean_function=None, Y_metadata=None, KL_scale=1.0, batch_scale=1.0):

        num_inducing = Z.shape[0]
        num_data, num_outputs = Y.shape

        #expand cholesky representation
        L = choleskies.flat_to_triang(q_u_chol)
        S = np.einsum('ijk,ljk->ilk', L, L) #L.dot(L.T)
        #Si,_ = linalg.dpotri(np.asfortranarray(L), lower=1)
        Si = choleskies.multiple_dpotri(L)
        logdetS = np.array([2.*np.sum(np.log(np.abs(np.diag(L[:,:,i])))) for i in range(L.shape[-1])])

        if np.any(np.isinf(Si)):
            raise ValueError("Cholesky representation unstable")
            #S = S + np.eye(S.shape[0])*1e-5*np.max(np.max(S))
            #Si, Lnew, _,_ = linalg.pdinv(S)

        #compute mean function stuff
        if mean_function is not None:
            prior_mean_u = mean_function.f(Z)
            prior_mean_f = mean_function.f(X)
        else:
            prior_mean_u = np.zeros((num_inducing, num_outputs))
            prior_mean_f = np.zeros((num_data, num_outputs))


        #compute kernel related stuff
        Kmm = kern.K(Z)
        Knm = kern.K(X, Z)
        Knn_diag = kern.Kdiag(X)
        Kmmi, Lm, Lmi, logdetKmm = linalg.pdinv(Kmm)

        #compute the marginal means and variances of q(f)
        A = np.dot(Knm, Kmmi)
        mu = prior_mean_f + np.dot(A, q_u_mean - prior_mean_u)
        v = Knn_diag[:,None] - np.sum(A*Knm,1)[:,None] + np.sum(A[:,:,None] * np.einsum('ij,jkl->ikl', A, S),1)

        #compute the KL term
        Kmmim = np.dot(Kmmi, q_u_mean)
        KLs = -0.5*logdetS -0.5*num_inducing + 0.5*logdetKmm + 0.5*np.einsum('ij,ijk->k', Kmmi, S) + 0.5*np.sum(q_u_mean*Kmmim,0)
        KL = KLs.sum()
        #gradient of the KL term (assuming zero mean function)
        dKL_dm = Kmmim.copy()
        dKL_dS = 0.5*(Kmmi[:,:,None] - Si)
        dKL_dKmm = 0.5*num_outputs*Kmmi - 0.5*Kmmi.dot(S.sum(-1)).dot(Kmmi) - 0.5*Kmmim.dot(Kmmim.T)

        if mean_function is not None:
            #adjust KL term for mean function
            Kmmi_mfZ = np.dot(Kmmi, prior_mean_u)
            KL += -np.sum(q_u_mean*Kmmi_mfZ)
            KL += 0.5*np.sum(Kmmi_mfZ*prior_mean_u)

            #adjust gradient for mean fucntion
            dKL_dm -= Kmmi_mfZ
            dKL_dKmm += Kmmim.dot(Kmmi_mfZ.T)
            dKL_dKmm -= 0.5*Kmmi_mfZ.dot(Kmmi_mfZ.T)

            #compute gradients for mean_function
            dKL_dmfZ = Kmmi_mfZ - Kmmim

        #quadrature for the likelihood
        F, dF_dmu, dF_dv, dF_dthetaL = likelihood.variational_expectations(Y, mu, v, Y_metadata=Y_metadata)

        #rescale the F term if working on a batch
        F, dF_dmu, dF_dv =  F*batch_scale, dF_dmu*batch_scale, dF_dv*batch_scale
        if dF_dthetaL is not None:
            dF_dthetaL =  dF_dthetaL.sum(1).sum(1)*batch_scale

        #derivatives of expected likelihood, assuming zero mean function
        Adv = A.T[:,:,None]*dF_dv[None,:,:] # As if dF_Dv is diagonal
        Admu = A.T.dot(dF_dmu)
        AdvA = np.dstack([np.dot(A.T, Adv[:,:,i].T) for i in range(num_outputs)])
        tmp = np.einsum('ijk,jlk->il', AdvA, S).dot(Kmmi)
        dF_dKmm = -Admu.dot(Kmmim.T) + AdvA.sum(-1) - tmp - tmp.T
        dF_dKmm = 0.5*(dF_dKmm + dF_dKmm.T) # necessary? GPy bug?
        tmp = 2.*(np.einsum('ij,jlk->ilk', Kmmi,S) - np.eye(num_inducing)[:,:,None])
        dF_dKmn = np.einsum('ijk,jlk->il', tmp, Adv) + Kmmim.dot(dF_dmu.T)
        dF_dm = Admu
        dF_dS = AdvA

        #adjust gradient to account for mean function
        if mean_function is not None:
            dF_dmfX = dF_dmu.copy()
            dF_dmfZ = -Admu
            dF_dKmn -= np.dot(Kmmi_mfZ, dF_dmu.T)
            dF_dKmm += Admu.dot(Kmmi_mfZ.T)


        #sum (gradients of) expected likelihood and KL part
        log_marginal = F.sum() - KL
        dL_dm, dL_dS, dL_dKmm, dL_dKmn = dF_dm - dKL_dm, dF_dS- dKL_dS, dF_dKmm- dKL_dKmm, dF_dKmn

        dL_dchol = np.dstack([2.*np.dot(dL_dS[:,:,i], L[:,:,i]) for i in range(num_outputs)])
        dL_dchol = choleskies.triang_to_flat(dL_dchol)

        grad_dict = {'dL_dKmm':dL_dKmm, 'dL_dKmn':dL_dKmn, 'dL_dKdiag': dF_dv.sum(1), 'dL_dm':dL_dm, 'dL_dchol':dL_dchol, 'dL_dthetaL':dF_dthetaL}
        if mean_function is not None:
            grad_dict['dL_dmfZ'] = dF_dmfZ - dKL_dmfZ
            grad_dict['dL_dmfX'] = dF_dmfX
        return Posterior(mean=q_u_mean, cov=S, K=Kmm, prior_mean=prior_mean_u), log_marginal, grad_dict