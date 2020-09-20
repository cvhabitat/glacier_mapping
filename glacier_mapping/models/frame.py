#!/usr/bin/env python
"""
Frame to Combine Model with Optimizer

This wraps the model and optimizer objects needed in training, so that each
training step can be concisely called with a single method (optimize).
"""
from pathlib import Path
import os
import torch
import numpy as np
from torch.optim.lr_scheduler import ReduceLROnPlateau
from .metrics import *
from .reg import *
from .unet import *
from .unet_dropout import *


class Framework:
    """
    Class to Wrap all the Training Steps

    """

    def __init__(self, loss_fn=None, model_opts=None, optimizer_opts=None,
                 reg_opts=None,):
        """
        Set Class Attrributes
        """
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.multi_class = True if model_opts.args.outchannels > 1 else False
        if loss_fn is None:
            loss_fn = torch.nn.CrossEntropyLoss() if self.multi_class else torch.nn.BCEWithLogitsLoss()
        self.loss_fn = loss_fn.to(self.device)

        if model_opts.name in ["Unet", "UnetDropout"]:
            model_def = globals()[model_opts.name]
        else:
            raise ValueError("Unknown model name")

        self.model = model_def(**model_opts.args).to(self.device)
        optimizer_def = getattr(torch.optim, optimizer_opts.name)
        self.optimizer = optimizer_def(self.model.parameters(), **optimizer_opts.args)
        self.lrscheduler = ReduceLROnPlateau(self.optimizer, "min",
                                             verbose=True, patience=500,
                                             min_lr=1e-6)
        self.reg_opts = reg_opts


    def optimize(self, x, y):
        """
        Take a single gradient step

        Args:
            X: raw training data
            y: labels
        Return:
            optimization
        """
        x = x.permute(0, 3, 1, 2).to(self.device)
        y = y.permute(0, 3, 1, 2).to(self.device)

        self.optimizer.zero_grad()
        y_hat = self.model(x)
        loss = self.calc_loss(y_hat, y)
        loss.backward()
        self.optimizer.step()
        return y_hat.permute(0, 2, 3, 1), loss.item()

    def val_operations(self, val_loss):
        """
        Update the LR Scheduler
        """
        self.lrscheduler.step(val_loss)

    def save(self, out_dir, epoch):
        """
        Save a model checkpoint
        """
        if not os.path.exists(out_dir):
            os.makedirs(out_dir)

        model_path = Path(out_dir, f"model_{epoch}.pt")
        optim_path = Path(out_dir, f"optim_{epoch}.pt")
        torch.save(self.model.state_dict(), model_path)
        torch.save(self.optimizer.state_dict(), optim_path)

    def infer(self, x):
        """ Make a prediction for a given x

        Args:
            x: input x

        Return:
            Prediction

        """
        x = x.permute(0, 3, 1, 2).to(self.device)
        with torch.no_grad():
            return self.model(x).permute(0, 2, 3, 1)

    def calc_loss(self, y_hat, y):
        """ Compute loss given a prediction

        Args:
            y_hat: Prediction
            y: Label

        Return:
            Loss values

        """
        y_hat = torch.tensor(y_hat, dtype=torch.long, device=self.device)
        y = y.to(self.device)
        if self.multi_class:
            target = torch.argmax(y, dim=1)
        else: traget = y
        loss = self.loss_fn(y_hat, target)
        for reg_type in self.reg_opts.keys():
            reg_fun = globals()[reg_type]
            penalty = reg_fun(
                self.model.parameters(),
                self.reg_opts[reg_type],
                self.device
            )
            loss += penalty

        return loss


    def metrics(self, y_hat, y, metrics_opts):
        """ Loop over metrics in train.yaml

        Args:
            y_hat: Predictions
            y: Labels
            metrics_opts: Metrics specified in the train.yaml

        Return:
            results

        """
        y_hat = y_hat.to(self.device)
        y = y.to(self.device)

        results = {}
        for k, metric in metrics_opts.items():
            if "threshold" in metric.keys():
                y_hat = y_hat > metric["threshold"]

                metric_fun = globals()[k]
                metric_value = metric_fun(y_hat, y)
            results[k] = np.mean(np.asarray(metric_value))
        return results
