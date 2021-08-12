#!/usr/bin/env python
# coding: utf-8

import inspect
import torch
from torch.autograd import Function
import numpy as np
import multiprocessing as mp
from pathos.multiprocessing import ProcessingPool
import spo
from spo.model import optModel

def solveWithObj4Par(cost, args, model_name):
    """
    A global function to solve function in parallel processors

    Args:
        cost (ndarray): cost of objective function
        args (dict): optModel args
        model_name (str): optModel class name

    Returns:
        list: optimal solution
    """
    # rebuild model
    try:
        model = eval(model_name)(**args)
    except:
        model = eval('spo.model.{}'.format(model_name))(**args)
    # set obj
    model.setObj(cost)
    # solve
    sol, _ = model.solve()
    return sol


def getArgs(model):
    """
    A global function to get args of model

    Args:
        model (optModel): optimization model

    Return:
        dict: model args
    """
    for mem in inspect.getmembers(model):
        if mem[0] == '__dict__':
            attrs = mem[1]
            args = {}
            for name in attrs:
                if name in inspect.signature(model.__init__).parameters:
                    args[name] = attrs[name]
            return args


class blackboxOpt(Function):
    """
    block-box optimizer function, which is diffenretiable to introduce
    blocks into neural networks.

    For block-box, the objective function is linear and constraints are
    known and fixed, but the cost vector need to be predicted from
    contextual data.

    The block-box approximate gradient of optimizer smoothly. Thus,
    allows us to design an algorithm based on stochastic gradient
    descent.

    Args:
        model (optModel): optimization model
        lambd (float): Black-Box parameters for function smoothing
        processes (int): number of processors, 1 for single-core, 0 for number of CPUs
    """
    def __init__(self, model, lambd=10, processes=1):
        super().__init__()
        # optimization model
        assert isinstance(model, optModel), 'arg model is not an optModel.'
        global _SPO_FUNC_BB_OPTMODEL
        _SPO_FUNC_BB_OPTMODEL = model
        # smoothing parameter
        assert lambd > 0, 'lambda is not positive.'
        global _SPO_FUNC_BB_LAMBDA
        _SPO_FUNC_BB_LAMBDA = lambd
        # num of processors
        assert processes in range(mp.cpu_count()), IndexError('Invalid processors number.')
        global _SPO_FUNC_BB_PROCESSES
        _SPO_FUNC_BB_PROCESSES = processes
        print('Num of cores: {}'.format(_SPO_FUNC_BB_PROCESSES))

    @staticmethod
    def forward(ctx, pred_cost):
        """
        Forward pass in neural network.

        Args:
            pred_cost: predicted costs

        Returns:
            tensor: predicted solutions
        """
        ins_num = len(pred_cost)
        # get device
        device = pred_cost.device
        # get global
        model = _SPO_FUNC_BB_OPTMODEL
        lambd = _SPO_FUNC_BB_LAMBDA
        processes = _SPO_FUNC_BB_PROCESSES
        # convert tenstor
        cp = pred_cost.to('cpu').numpy()
        # single-core
        if processes == 1:
            sol = []
            for i in range(ins_num):
                # solve
                model.setObj(cp[i])
                solp, _ = model.solve()
                sol.append(solp)
        # multi-core
        else:
            # number of processes
            processes = mp.cpu_count() if not processes else processes
            # get class
            model_name = type(model).__name__
            # get args
            args = getArgs(model)
            # parallel computing
            with ProcessingPool(processes) as pool:
                sol = pool.amap(solveWithObj4Par, cp, [args]*ins_num, [model_name]*ins_num).get()
        # convert to tensor
        pred_sol = torch.FloatTensor(sol).to(device)
        # save
        ctx.save_for_backward(pred_cost, pred_sol)
        return pred_sol

    @staticmethod
    def backward(ctx, grad_output):
        """
        Backward pass in neural network
        """
        pred_cost, pred_sol = ctx.saved_tensors
        ins_num = len(pred_cost)
        # get device
        device = pred_cost.device
        # get global
        model = _SPO_FUNC_BB_OPTMODEL
        lambd = _SPO_FUNC_BB_LAMBDA
        processes = _SPO_FUNC_BB_PROCESSES
        # convert tenstor
        cp = pred_cost.to('cpu').numpy()
        wp = pred_sol.to('cpu').numpy()
        dl = grad_output.to('cpu').numpy()
        # perturbed costs
        cq = cp + lambd * dl
        # single-core
        if processes == 1:
            grad = []
            for i in range(len(cp)):
                # solve
                model.setObj(cq[i])
                solq, _ = model.solve()
                # gradient of continuous interpolation
                grad.append((solq - wp[i]) / lambd)
        # multi-core
        else:
            # get class
            model_name = type(model).__name__
            # get args
            args = getArgs(model)
            # number of processes
            processes = mp.cpu_count() if not processes else processes
            # parallel computing
            with ProcessingPool(processes) as pool:
                sol = pool.amap(solveWithObj4Par, cq, [args]*ins_num, [model_name]*ins_num).get()
            # get gradient
            grad = []
            for i in range(ins_num):
                grad.append((sol[i] - wp[i]) / lambd)
        grad = torch.FloatTensor(grad).to(device)
        return grad
