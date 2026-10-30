"""Make null units false under Python 3.

Scripts test units with "if un:" / "if not un:" to mean "is this a real
unit?".  The engine binds that check as __nonzero__, which only Python 2
calls; Python 3 looks for __bool__, finds none, and treats every unit,
null or not, as true.  That silently breaks code such as the random
encounter spawner, which waits for "not significant_unit" before it ever
looks for a base to launch ships near.

All scripts share one interpreter, so setting __bool__ on VS.Unit once
fixes the test everywhere.  This module is imported for that side effect
by vsrandom and debug, which nearly every script pulls in.  Engines that
already bind __bool__ are left alone.
"""
import VS

if '__bool__' not in VS.Unit.__dict__:
    VS.Unit.__bool__ = lambda self: not self.isNull()
