#!/usr/bin/env python

# @file:    optimize.py
# @purpose: read a ROOT file containing ntuples and attempt to find optimal cuts
# @author:  Giordon Stark <gstark@cern.ch>
# @date:    June 2015
#

# __future__ imports must occur at beginning of file
# redirect python output using the newer print function with file description
#   print(string, f=fd)
from __future__ import print_function
# used to redirect ROOT output
#   see http://stackoverflow.com/questions/21541238/get-ipython-doesnt-work-in-a-startup-script-for-ipython-ipython-notebook
import tempfile

import os, sys
# grab the stdout and have python write to this instead
# ROOT will write to the original stdout
STDOUT = os.fdopen(os.dup(sys.stdout.fileno()), 'w')

# for logging, set it up
import logging
root_logger = logging.getLogger()
root_logger.addHandler(logging.StreamHandler(STDOUT))
logger = logging.getLogger("optimize")

# import all libraries
import argparse
import subprocess
import json
import hashlib
import copy

'''
  with tempfile.NamedTemporaryFile() as tmpFile:
    if not args.root_verbose:
      ROOT.gSystem.RedirectOutput(tmpFile.name, "w")

    # execute code here

    if not args.root_verbose:
      ROOT.gROOT.ProcessLine("gSystem->RedirectOutput(0);")
'''

# Set up ROOT
import ROOT

#root_numpy
import root_numpy as rnp
import numpy as np


def format_arg_value(arg_val):
  """ Return a string representing a (name, value) pair.

  >>> format_arg_value(('x', (1, 2, 3)))
  'x=(1, 2, 3)'
  """
  arg, val = arg_val
  return "%s=%r" % (arg, val)

# http://wordaligned.org/articles/echo
def echo(*echoargs, **echokwargs):
  logger.debug(echoargs)
  logger.debug(echokwargs)
  def echo_wrap(fn):
    """ Echo calls to a function.

    Returns a decorated version of the input function which "echoes" calls
    made to it by writing out the function's name and the arguments it was
    called with.
    """

    # Unpack function's arg count, arg names, arg defaults
    code = fn.func_code
    argcount = code.co_argcount
    argnames = code.co_varnames[:argcount]
    fn_defaults = fn.func_defaults or list()
    argdefs = dict(zip(argnames[-len(fn_defaults):], fn_defaults))

    def wrapped(*v, **k):
      # Collect function arguments by chaining together positional,
      # defaulted, extra positional and keyword arguments.
      positional = map(format_arg_value, zip(argnames, v))
      defaulted = [format_arg_value((a, argdefs[a]))
                   for a in argnames[len(v):] if a not in k]
      nameless = map(repr, v[argcount:])
      keyword = map(format_arg_value, k.items())
      args = positional + defaulted + nameless + keyword
      write("%s(%s)\n" % (fn.__name__, ", ".join(args)))
      return fn(*v, **k)
    return wrapped

  write = echokwargs.get('write', sys.stdout.write)
  if len(echoargs) == 1 and callable(echoargs[0]):
    return echo_wrap(echoargs[0])
  return echo_wrap

#@echo(write=logger.debug)
def apply_cut(arr, pivot, direction):
  """ Given a numpy array of values, apply a cut in the direction of expected signal

  >>> apply_cut(np.random.randn(100), 0, '>')  # only positive values (val > cut)
  >>> apply_cut(np.random.randn(100), 0, '<')  # only negative values (val < cut)
  """
  if direction == '<' and pivot is not None:
    return arr < pivot
  elif direction == '>' and pivot is not None:
    return arr > pivot
  else:
    return np.ones(arr.shape, dtype=bool)

#@echo(write=logger.debug)
def apply_cuts(arr, cuts):
  return reduce(np.bitwise_and, (apply_cut(arr[cut['branch']], cut.get('pivot', None), cut.get('signal_direction', None)) for cut in cuts))

#@echo(write=logger.debug)
def get_cut(superCuts, index=0):
  # reached bottom of iteration, yield what we've done
  if index >= len(superCuts): yield superCuts
  else:
    # start of iteration, make a copy of the input dictionary
    # if index == 0: superCuts = copy.deepcopy(superCuts)
    # reference to item
    item = superCuts[index]
    for pivot in np.arange(item['min'], item['max'], item['step']):
      # set the pivot value
      item['pivot'] = pivot
      # recursively call, yield the result which is the superCuts
      for cut in get_cut(superCuts, index+1): yield cut

#@echo(write=logger.debug)
def get_cut_hash(cut):
  return hashlib.md5(str([sorted(obj.items()) for obj in cut])).hexdigest()

#@echo(write=logger.debug)
def count_events(tree, cuts, eventWeightBranch):
  return np.sum(tree[apply_cuts(tree, cuts)][eventWeightBranch])

#@echo(write=logger.debug)
def get_significance(signal, bkgd, cuts):
  numSignal = np.sum(signal[args.eventWeightBranch].take(apply_cuts(signal, cuts)))
  numBkgd   = np.sum(bkgd[args.eventWeightBranch].take(apply_cuts(bkgd, cuts)))
  return ROOT.RooStats.NumberCountingUtils.BinomialExpZ(numSignal, numBkgd, args.bkgdUncertainty)

if __name__ == "__main__":
  class CustomFormatter(argparse.ArgumentDefaultsHelpFormatter):
    pass

  __version__ = subprocess.check_output(["git", "describe", "--always"], cwd=os.path.dirname(os.path.realpath(__file__))).strip()
  __short_hash__ = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=os.path.dirname(os.path.realpath(__file__))).strip()

  parser = argparse.ArgumentParser(description='Become an accountant and cook the books!',
                                   usage='%(prog)s filename [filename] [options]',
                                   formatter_class=lambda prog: CustomFormatter(prog, max_help_position=30))

  parser = argparse.ArgumentParser(description='Process ROOT ntuples and Optimize Cuts. v.{0}'.format(__version__),
                                  usage='%(prog)s --signal filename ... --bkgd filename ... [options]',
                                  formatter_class=lambda prog: CustomFormatter(prog, max_help_position=30))

  # positional argument, require the first argument to be the input filename
  parser.add_argument('--signal',
                      required=True,
                      type=str,
                      nargs='+',
                      metavar='<files>',
                      help='signal ntuples')
  parser.add_argument('--bkgd',
                      required=True,
                      type=str,
                      nargs='+',
                      metavar='<files>',
                      help='background ntuples')
  parser.add_argument('--cuts',
                      required=False,
                      type=str,
                      metavar='<file>',
                      help='json dict of cuts to optimize over',
                      default='supercuts.json')
  # these are options allowing for various additional configurations in filtering container and types to dump
  parser.add_argument('--tree',
                      type=str,
                      required=False,
                      dest='tree_name',
                      metavar='<tree name>',
                      help='Specify the tree that contains the StoreGate structure.',
                      default='oTree')
  parser.add_argument('--globalMinVal',
                      type=float,
                      required=False,
                      dest='globalMinVal',
                      metavar='<min val>',
                      help='Specify the minimum value of which to exclude completely when analyzing branch-by-branch.',
                      default=-99.0)
  parser.add_argument('--bkgdUncertainty',
                      type=float,
                      required=False,
                      dest='bkgdUncertainty',
                      metavar='<sigma>',
                      help='Specify the background uncertainty for calculating significance using BinomialExpZ',
                      default=0.3)
  parser.add_argument('--eventWeight',
                      type=str,
                      required=False,
                      dest='eventWeightBranch',
                      metavar='<branch name>',
                      help='Specify a different branch that contains the event weight',
                      default='event_weight')

  '''general arguments for verbosity'''
  parser.add_argument('-v',
                      '--verbose',
                      dest='verbose',
                      action='count',
                      default=0,
                      help='Enable verbose output of various levels. Use --debug-root to enable ROOT debugging.')
  parser.add_argument('--debug-root',
                      dest='root_verbose',
                      action='store_true',
                      help='Enable ROOT debugging/output.')
  parser.add_argument('-b',
                      '--batch',
                      dest='batch_mode',
                      action='store_true',
                      help='Enable batch mode for ROOT. ')

  parser.add_argument('-i',
                      '--interactive',
                      dest='interactive',
                      action='store_true',
                      help='(INACTIVE) Flip on/off interactive mode allowing you to navigate through the container types and properties.')

  # parse the arguments, throw errors if missing any
  args = parser.parse_args()

  try:
    # start execution of actual program
    import timing

    # set verbosity for python printing
    if args.verbose < 5:
      logger.setLevel(25 - args.verbose*5)
    else:
      logger.setLevel(logging.NOTSET + 1)

    with tempfile.NamedTemporaryFile() as tmpFile:
      if not args.root_verbose:
        ROOT.gSystem.RedirectOutput(tmpFile.name, "w")

      # if flag is shown, set batch_mode to true, else false
      ROOT.gROOT.SetBatch(args.batch_mode)

      # this is a dict that holds all the trees
      trees = {'signal': None, 'bkgd': None}

      for group in ['signal', 'bkgd']:
        logger.info("Initializing TChain: {0}".format(group))
        # start by making a TChain
        trees[group] = ROOT.TChain(args.tree_name)
        for fname in vars(args).get(group, []):
          if not os.path.isfile(fname):
            raise ValueError('The supplied input file `{0}` does not exist or I cannot find it.'.format(fname))
          else:
            logger.info("\tAdding {0}".format(fname))
            trees[group].Add(fname)

        # Print some information
        logger.info('Number of input events: %s' % trees[group].GetEntries())

      signalBranches = set(i.GetName() for i in trees['signal'].GetListOfBranches())
      bkgdBranches = set(i.GetName() for i in trees['bkgd'].GetListOfBranches())

      if not signalBranches == bkgdBranches:
        raise ValueError('The signal and background trees do not have the same branches!')

      # we have our branches
      branches = signalBranches
      # clear our variable
      signalBranches = bkgdBranches = None

      logger.info("The signal and background trees have the same branches.")

      # get signal and background trees
      signal = rnp.tree2array(trees['signal'])
      bkgd = rnp.tree2array(trees['bkgd'])

      for b in sorted(branches):
        skipSignal = signal[b] < args.globalMinVal
        skipBkgd = bkgd[b] < args.globalMinVal

        signalPercentile = np.percentile(signal[b][~skipSignal], [0., 25., 50., 75., 100.])
        bkgdPercentile = np.percentile(bkgd[b][~skipBkgd], [0., 25., 50., 75., 100.])
        prelimStr = "{0}\n\tSignal ({1:6d} skipped):\t{2[0]:12.2f}\t{2[1]:12.2f}\t{2[2]:12.2f}\t{2[3]:12.2f}\t{2[4]:12.2f}\n\tBkgd   ({3:6d} skipped):\t{4[0]:12.2f}\t{4[1]:12.2f}\t{4[2]:12.2f}\t{4[3]:12.2f}\t{4[4]:12.2f}"

        logger.info(prelimStr.format(b, np.sum(skipSignal), signalPercentile, np.sum(skipBkgd), bkgdPercentile))

      # now read the cuts file and start optimizing
      logger.info("Opening {0} for reading".format(args.cuts))
      with open(args.cuts) as cuts_file:
        data = json.load(cuts_file)

      # hold dictionary of hash as key, and significance as value
      significances = {}
      logger.log(25, "Calculating significance for a variety of cuts")
      for cut in get_cut(copy.deepcopy(data)):
        cut_hash = get_cut_hash(cut)
        cut_significance = get_significance(signal, bkgd, cut)
        significances[cut_hash] = cut_significance
        logger.info("\t{0:32s}\t{1:4.2f}".format(cut_hash, cut_significance))

      logger.log(25, "Calculated significance for {0:d} cuts".format(len(significances)))

      if not args.root_verbose:
        ROOT.gROOT.ProcessLine("gSystem->RedirectOutput(0);")

  except Exception, e:
    # stop redirecting if we crash as well
    if not args.root_verbose:
      ROOT.gROOT.ProcessLine("gSystem->RedirectOutput(0);")

    logger.exception("{0}\nAn exception was caught!".format("-"*20))
