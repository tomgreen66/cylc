#!/bin/bash

# CYLC USER LOGIN SCRIPT EXAMPLE. Copy the following to your .profile
# and adapt according to your preferences and local cylc installation.

# These environment variables are required for interactive cylc usage. 
# Access to cylc by running tasks is automatically configured by cylc.
#_______________________________________________________________________
# Add the cylc bin directory to $PATH. The 'cylc' and 'gcylc' commands
# configure access to cylc sub-commands and python modules at run time.
export PATH=/path/to/cylc/bin:$PATH
#_______________________________________________________________________
# For 'cylc edit' or gcylc -> Edit, set terminal and GUI editors:
export EDITOR=vim
export GEDITOR='gvim -f'
# (See 'cylc edit --help' for examples of other editors).
#_______________________________________________________________________
# To access the Cylc User Guide via the gcylc GUI menus:
export PDF_READER=evince
export HTML_READER=firefox
# (The HTML guide is opened via file path, not http URL).
#_______________________________________________________________________
# Some cylc commands require a writeable temporary directory. This is 
# now determined automatically by Python's tempfile.mkdtemp() which is 
# likely to use $TMPDIR if it is defined. You can override this if
# necessary by setting the environment variable CYLC_TMPDIR:
  #==># export CYLC_TMPDIR=$TMPDIR/cylc
  #==># mkdir -p $CYLC_TMPDIR
# (Use of plain TMPDIR reportedly causes problems under KDE desktop)
# In the default (non-CYLC_TMPDIR) case the directory will be cleaned up 
# when the relevant cylc command exits.
#_______________________________________________________________________
# For a local user install of one or more of Pyro, Graphviz, Pygraphviz,
# and Jinja2 (if you can't easily get them installed at system level on 
# the cylc host) follow Cylc User Guide Installation instructions on how
# to install them locally, and modify your PYTHONPATH accordingly, e.g.:
## PYTHONPATH=$HOME/external/lib64/python2.6/site-packages:$PYTHONPATH
## export PYTHONPATH
