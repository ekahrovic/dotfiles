'''track large binary files

Large binary files tend to be not very compressible, not very "diffable",
and not at all mergeable.  Such files are not handled well by Mercurial\'s
storage format (revlog), which is based on compressed binary deltas.
bfiles solves this problem by adding a centralized client-server layer on
top of Mercurial: big files live in a *central store* out on the network
somewhere, and you only fetch the big files that you need when you need
them.

bfiles works by maintaining a *standin* in .hgbfiles/ for each big file.
The standins are small (41 bytes: an SHA-1 hash plus newline) and are
tracked by Mercurial.  Big file revisions are identified by the SHA-1 hash
of their contents, which is written to the standin.  bfiles uses that
revision ID to get/put big file revisions from/to the central store.

A complete tutorial for using bfiles is included in ``usage.txt`` in the
bfiles source distribution.  See
http://vc.gerg.ca/hg/hg-bfiles/raw-file/tip/usage.txt for the latest
version.
'''

from mercurial import commands
import bfsetup
import bfcommands

reposetup = bfsetup.reposetup
uisetup = bfsetup.uisetup

commands.norepo += " kbfconvert"

cmdtable = bfcommands.cmdtable
