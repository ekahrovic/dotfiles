'''Setup code for bfiles extension: reposetup(), uisetup().'''

import os
import types
import copy

from mercurial import hg, extensions, commands, util, context, cmdutil, \
        match as match_, filemerge, node, archival, httprepo, error
from mercurial.i18n import _
from mercurial.node import hex
import bfutil, bfcommands

def hgversion():
    from mercurial.__version__ import version
    return [int(n) for n in version.partition('+')[0].split('.')]
hgversion = hgversion()

# -- Wrappers: modify existing commands --------------------------------

def reposetup(ui, repo):
    # add a kbfiles-specific querystring argument to remote requests, so kiln can reject
    # operations on a kbfiles-enabled remote repo from a non-kbfiles local repo.
    if issubclass(repo.__class__, httprepo.httprepository):
        class kbfilesrepo(repo.__class__):
            # The function we want to override is do_cmd for Mercurial <= 1.6
            # and _callstream for Mercurial > 1.6. Wrap whichever one we can find.
            if hasattr(repo.__class__, 'do_cmd'):
                def do_cmd(self, cmd, **args):
                    args['kbfiles'] = 'true'
                    return super(repo.__class__, self).do_cmd(cmd, **args)
            if hasattr(repo.__class__, '_callstream'):
                def _callstream(self, cmd, **args):
                    args['kbfiles'] = 'true'
                    return super(repo.__class__, self)._callstream(cmd, **args)
        repo.__class__ = kbfilesrepo

    # bfiles doesn't support non-local repositories -- get out quick in
    # such a case
    if not repo.local():
        return

    for name in ('status', 'commitctx', 'commit', 'push'):
        method = getattr(repo, name)
        #if not (isinstance(method, types.MethodType) and
        #        method.im_func is repo.__class__.commitctx.im_func):
        if isinstance(method, types.FunctionType) and method.func_name == 'wrap':
            ui.warn(_('kbfiles: repo method %r appears to have already been '
                    'wrapped by another extension: '
                    'kbfiles may behave incorrectly\n')
                    % name)

    class bfiles_repo(repo.__class__):
        bfstatus = False
        def status_nobfiles(self, *args, **kwargs):
            return super(bfiles_repo, self).status(*args, **kwargs)

        # Figure out the status of big files and insert them into the
        # appropriate list in the result. Also removes standin files from
        # the listing. This function reverts to the original status if
        # self.bfstatus is False
        def status(self, node1='.', node2=None, match=None, ignored=False, clean=False, unknown=False, subrepos=None):
            listignored, listclean, listunknown = ignored, clean, unknown
            if not self.bfstatus:
                try:
                    return super(bfiles_repo, self).status(node1, node2, match, listignored, listclean, listunknown, subrepos)
                except TypeError:
                    return super(bfiles_repo, self).status(node1, node2, match, listignored, listclean, listunknown)
            else:
                # some calls in this function rely on the old version of status
                self.bfstatus = False
                if isinstance(node1, context.changectx):
                    ctx1 = node1
                else:
                    ctx1 = repo[node1]
                if isinstance(node2, context.changectx):
                    ctx2 = node2
                else:
                    ctx2 = repo[node2]
                working = ctx2.rev() is None
                parentworking = working and ctx1 == self['.']

                def inctx(file, ctx):
                    try:
                        if ctx.rev() is None:
                            return file in ctx.manifest()
                        ctx[file]
                        return True
                    except:
                        return False

                # create a copy of match that matches standins instead of bfiles
                # if matcher not set then it is the always matcher so overwrite that
                if match is None:
                    match = match_.always(self.root, self.getcwd())

                def tostandin(file):
                    if inctx(bfutil.standin(file), ctx2):
                        return bfutil.standin(file)
                    return file

                m = copy.copy(match)
                m._files = [tostandin(f) for f in m._files]

                # get ignored clean and unknown but remove them later if they were not asked for
                try:
                    result = super(bfiles_repo, self).status(node1, node2, m, True, True, True, subrepos)
                except TypeError:
                    result = super(bfiles_repo, self).status(node1, node2, m, True, True, True)
                if working:
                    # Hold the wlock while we read bfiles and update the bfdirstate
                    wlock = repo.wlock()
                    try:
                        # Any non bfiles that were explicitly listed must be taken out or
                        # bfdirstate.status will report an error. The status of these files
                        # was already computed using super's status.
                        bfdirstate = bfutil.open_bfdirstate(ui, self)
                        match._files = [f for f in match._files if f in bfdirstate]
                        s = bfdirstate.status(match, [], listignored, listclean, listunknown)
                        (unsure, modified, added, removed, missing, unknown, ignored, clean) = s
                        if parentworking:
                            for bfile in unsure:
                                if ctx1[bfutil.standin(bfile)].data().strip() != bfutil.hashfile(self.wjoin(bfile)):
                                    modified.append(bfile)
                                else:
                                    clean.append(bfile)
                                    bfdirstate.normal(bfutil.unixpath(bfile))
                            bfdirstate.write()
                        else:
                            tocheck = unsure + modified + added + clean
                            modified, added, clean = [], [], []

                            for bfile in tocheck:
                                standin = bfutil.standin(bfile)
                                if inctx(standin, ctx1):
                                    if ctx1[standin].data().strip() != bfutil.hashfile(self.wjoin(bfile)):
                                        modified.append(bfile)
                                    else:
                                        clean.append(bfile)
                                else:
                                    added.append(bfile)
                    finally:
                        wlock.release()

                    for standin in ctx1.manifest():
                        if not bfutil.is_standin(standin):
                            continue
                        bfile = bfutil.split_standin(standin)
                        if not match(bfile):
                            continue
                        if bfile not in bfdirstate:
                            removed.append(bfile)
                    # Handle unknown and ignored differently
                    bfiles = (modified, added, removed, missing, [], [], clean)
                    result = list(result)
                    # Unknown files
                    result[4] = [f for f in unknown if repo.dirstate[f] == '?' and not bfutil.is_standin(f)]
                    # Ignored files must be ignored by both the dirstate and bfdirstate
                    result[5] = set(ignored).intersection(set(result[5]))
                    # combine normal files and bfiles
                    normals = [[fn for fn in filelist if not bfutil.is_standin(fn)] for filelist in result]
                    result = [sorted(list1 + list2) for (list1, list2) in zip(normals, bfiles)]
                else:
                    def toname(f):
                        if bfutil.is_standin(f):
                            return bfutil.split_standin(f)
                        return f
                    result = [[toname(f) for f in items] for items in result]

                if not listunknown:
                    result[4] = []
                if not listignored:
                    result[5] = []
                if not listclean:
                    result[6] = []
                self.bfstatus = True
                return result

        # This call happens after a commit has occurred. Copy all of the bfiles
        # into the cache
        def commitctx(self, *args, **kwargs):
            node = super(bfiles_repo, self).commitctx(*args, **kwargs)
            ctx = self[node]
            for filename in ctx.files():
                if bfutil.is_standin(filename) and filename in ctx.manifest():
                    realfile = bfutil.split_standin(filename)
                    bfutil.copy_to_cache(self, ctx.node(), realfile)

            return node

        # This call happens before a commit has occurred. The bfile standins
        # have not had their contents updated (to reflect the hash of their bfile).
        # Do that here.
        def commit(self, text="", user=None, date=None, match=None, force=False,
                   editor=False, extra={}):
            orig = super(bfiles_repo, self).commit

            wlock = repo.wlock()
            try:
                # Case 1: user calls commit with no specific files or
                # include/exclude patterns: refresh and commit everything.
                if (match is None) or (not match.anypats() and not match.files()):
                    bfiles = bfutil.list_bfiles(self)
                    bfdirstate = bfutil.open_bfdirstate(ui, self)
                    # this only loops through bfiles that exist (not removed/renamed)
                    for bfile in bfiles:
                        if os.path.exists(self.wjoin(bfutil.standin(bfile))):
                            bfutil.update_standin(self, bfutil.standin(bfile))
                            bfdirstate.normal(bfutil.unixpath(bfile))
                    for bfile in bfdirstate:
                        if not os.path.exists(repo.wjoin(bfutil.standin(bfile))):
                            bfdirstate.forget(bfutil.unixpath(bfile))
                    bfdirstate.write()

                    return orig(text=text, user=user, date=date, match=match,
                                    force=force, editor=editor, extra=extra)

                for file in match.files():
                    if bfutil.is_standin(file):
                        raise util.Abort("Don't commit bfile standin. Commit bfile.")

                # Case 2: user calls commit with specified patterns: refresh any
                # matching big files.
                smatcher = bfutil.compose_standin_matcher(self, match)
                standins = bfutil.dirstate_walk(self.dirstate, smatcher)

                # No matching big files: get out of the way and pass control to
                # the usual commit() method.
                if not standins:
                    return orig(text=text, user=user, date=date, match=match,
                                    force=force, editor=editor, extra=extra)

                # Refresh all matching big files.  It's possible that the commit
                # will end up failing, in which case the big files will stay
                # refreshed.  No harm done: the user modified them and asked to
                # commit them, so sooner or later we're going to refresh the
                # standins.  Might as well leave them refreshed.
                bfdirstate = bfutil.open_bfdirstate(ui, self)
                for standin in standins:
                    bfile = bfutil.split_standin(standin)
                    if bfdirstate[bfile] is not 'r':
                        bfutil.update_standin(self, standin)
                        bfdirstate.normal(bfutil.unixpath(bfile))
                    else:
                        bfdirstate.forget(bfutil.unixpath(bfile))
                bfdirstate.write()

                # Cook up a new matcher that only matches regular files or
                # standins corresponding to the big files requested by the user.
                # Have to modify _files to prevent commit() from complaining
                # "not tracked" for big files.
                bfiles = bfutil.list_bfiles(repo)
                match = copy.copy(match)
                orig_matchfn = match.matchfn

                # Check both the list of bfiles and the list of standins because if a bfile was removed, it
                # won't be in the list of bfiles at this point
                match._files += sorted(standins)

                actualfiles = []
                for f in match._files:
                    fstandin = bfutil.standin(f)

                    # Ignore known bfiles and standins
                    if f in bfiles or fstandin in standins:
                        continue

                    # Append directory separator to avoid collisions
                    if not fstandin.endswith(os.sep):
                        fstandin += os.sep

                    # Prevalidate matching standin directories
                    if any(st for st in match._files if st.startswith(fstandin)):
                        continue
                    actualfiles.append(f)
                match._files = actualfiles

                def matchfn(f):
                    if orig_matchfn(f):
                        return f not in bfiles
                    else:
                        return f in standins

                match.matchfn = matchfn
                return orig(text=text, user=user, date=date, match=match,
                                force=force, editor=editor, extra=extra)
            finally:
                wlock.release()

        def push(self, remote, force=False, revs=None, newbranch=False):
            o = bfutil.findoutgoing(repo, remote, force)
            if o:
                toupload = set()
                o = repo.changelog.nodesbetween(o, revs)[0]
                for n in o:
                    parents = [p for p in repo.changelog.parents(n) if p != node.nullid]
                    ctx = repo[n]
                    files = set(ctx.files())
                    if len(parents) == 2:
                        mc = ctx.manifest()
                        mp1 = ctx.parents()[0].manifest()
                        mp2 = ctx.parents()[1].manifest()
                        for f in mp1:
                            if f not in mc:
                                files.add(f)
                        for f in mp2:
                            if f not in mc:
                                files.add(f)
                        for f in mc:
                            if mc[f] != mp1.get(f, None) or mc[f] != mp2.get(f, None):
                                files.add(f)

                    toupload = toupload.union(set([ctx[f].data().strip() for f in files if bfutil.is_standin(f) and f in ctx]))
                bfcommands.upload_bfiles(ui, self, remote, toupload)
            # Mercurial >= 1.6 takes the newbranch argument, try that first.
            try:
                return super(bfiles_repo, self).push(remote, force, revs, newbranch)
            except TypeError:
                return super(bfiles_repo, self).push(remote, force, revs)

    repo.__class__ = bfiles_repo

# Add works by going through the files that the user wanted to add
# and checking if they should be added as bfiles. Then making a new
# matcher which matches only the normal files and running the original
# version of add.
def override_add(orig, ui, repo, *pats, **opts):
    bf = opts.pop('bf', None)

    bfsize = opts.pop('bfsize', None)
    if bfsize:
        try:
            bfsize = int(bfsize)
        except ValueError:
            raise util.Abort(_('size must be an integer, was %s\n') % bfsize)
    else:
        if os.path.exists(repo.wjoin(bfutil.short_name)):
            bfsize = ui.config(bfutil.long_name, 'size', default='10')
            if bfsize:
                try:
                    bfsize = int(bfsize)
                except ValueError:
                    raise util.Abort(_('bfiles.size must be integer, was %s\n') % bfsize)

    bfmatcher = None
    if os.path.exists(repo.wjoin(bfutil.short_name)):
        bfpats = ui.config(bfutil.long_name, 'patterns', default=())
        if bfpats:
            bfpats = bfpats.split(' ')
            bfmatcher = match_.match(repo.root, '', list(bfpats))

    bfnames = []
    m = cmdutil.match(repo, pats, opts)
    m.bad = lambda x,y: None
    wctx = repo[None]
    for f in repo.walk(m):
        exact = m.exact(f)
        bfile = bfutil.standin(f) in wctx
        nfile = f in wctx

        if exact and bfile:
            ui.warn(_('%s already a bfile\n') % f)
            continue
        # Don't warn the user when they attempt to add a normal tracked file. The normal add code
        # will do that for us.
        if exact and nfile:
            continue
        if exact or (not bfile and not nfile):
            if bf or (bfsize and os.path.getsize(repo.wjoin(f)) >= bfsize*1024*1024) \
                                            or (bfmatcher and bfmatcher(f)):
                bfnames.append(f)
                if ui.verbose or not exact:
                    ui.status(_('adding %s as bfile\n') % m.rel(f))

    bad = []
    standins = []

    # Need to lock otherwise there could be a race condition inbetween when standins are created
    # and added to the repo
    wlock = repo.wlock()
    try:
        if not opts.get('dry_run'):
            bfdirstate = bfutil.open_bfdirstate(ui, repo)
            for f in bfnames:
                standinname = bfutil.standin(f)
                bfutil.write_standin(repo, standinname, hash='', executable=bfutil.get_executable(repo.wjoin(f)))
                standins.append(standinname)
                if bfdirstate[bfutil.unixpath(f)] == 'r':
                    bfdirstate.normallookup(bfutil.unixpath(f))
                else:
                    bfdirstate.add(bfutil.unixpath(f))
            bfdirstate.write()
            bad += [bfutil.split_standin(f) for f in bfutil.repo_add(repo, standins) if f in m.files()]
    finally:
        wlock.release()

    oldmatch = cmdutil.match
    manifest = repo[None].manifest()
    def override_match(repo, pats=[], opts={}, globbed=False, default='relpath'):
        match = oldmatch(repo, pats, opts, globbed, default)
        m = copy.copy(match)
        notbfile = lambda f: not bfutil.is_standin(f) and bfutil.standin(f) not in manifest
        m._files = [f for f in m._files if notbfile(f)]
        m._fmap = set(m._files)
        orig_matchfn = m.matchfn
        m.matchfn = lambda f: notbfile(f) and orig_matchfn(f) or None
        return m
    cmdutil.match = override_match
    result = orig(ui, repo, *pats, **opts)
    cmdutil.match = oldmatch

    return (result is 1 or bad) and 1 or 0

def override_remove(orig, ui, repo, *pats, **opts):
    wctx = repo[None].manifest()
    oldmatch = cmdutil.match
    def override_match(repo, pats=[], opts={}, globbed=False, default='relpath'):
        match = oldmatch(repo, pats, opts, globbed, default)
        m = copy.copy(match)
        notbfile = lambda f: not bfutil.is_standin(f) and bfutil.standin(f) not in wctx
        m._files = [f for f in m._files if notbfile(f)]
        m._fmap = set(m._files)
        orig_matchfn = m.matchfn
        m.matchfn = lambda f: orig_matchfn(f) and notbfile(f)
        return m
    cmdutil.match = override_match
    orig(ui, repo, *pats, **opts)
    cmdutil.match = oldmatch

    after, force = opts.get('after'), opts.get('force')
    if not pats and not after:
        raise util.Abort(_('no files specified'))
    m = cmdutil.match(repo, pats, opts)
    try:
        repo.bfstatus = True
        s = repo.status(match=m, clean=True)
    finally:
        repo.bfstatus = False
    modified, added, deleted, clean = [[f for f in list if bfutil.standin(f) in wctx] for list in [s[0], s[1], s[3], s[6]]]

    def warn(files, reason):
        for f in files:
            ui.warn(_('not removing %s: file %s (use -f to force removal)\n')
                    % (m.rel(f), reason))

    if force:
        remove, forget = modified + deleted + clean, added
    elif after:
        remove, forget = deleted, []
        warn(modified + added + clean, _('still exists'))
    else:
        remove, forget = deleted + clean, []
        warn(modified, _('is modified'))
        warn(added, _('has been marked for add'))

    for f in sorted(remove + forget):
        if ui.verbose or not m.exact(f):
            ui.status(_('removing %s\n') % m.rel(f))

    # Need to lock because standin files are deleted then removed from the repository
    # and we could race inbetween.
    wlock = repo.wlock()
    try:
        bfdirstate = bfutil.open_bfdirstate(ui, repo)
        for f in remove:
            if not after:
                os.unlink(repo.wjoin(f))
                currentdir = os.path.split(f)[0]
                while currentdir and not os.listdir(repo.wjoin(currentdir)):
                    os.rmdir(repo.wjoin(currentdir))
                    currentdir = os.path.split(currentdir)[0]
            bfdirstate.remove(bfutil.unixpath(f))
        bfdirstate.write()

        forget = [bfutil.standin(f) for f in forget]
        remove = [bfutil.standin(f) for f in remove]
        bfutil.repo_forget(repo, forget)
        bfutil.repo_remove(repo, remove, unlink=True)
    finally:
        wlock.release()

def override_status(orig, ui, repo, *pats, **opts):
    try:
        repo.bfstatus = True
        return orig(ui, repo, *pats, **opts)
    finally:
        repo.bfstatus = False

def override_verify(orig, ui, repo, *pats, **opts):
    bf = opts.pop('bf', False)
    all = opts.pop('bfa', False)
    contents = opts.pop('bfc', False)

    result = orig(ui, repo, *pats, **opts)
    if bf:
        result = result or bfcommands.verify_bfiles(ui, repo, all, contents)
    return result

# Override needs to refresh standins so that update's normal merge
# will go through properly. Then the other update hook (overriding repo.update)
# will get the new files. Filemerge is also overriden so that the merge
# will merge standins correctly.
def override_update(orig, ui, repo, *pats, **opts):
    bfdirstate = bfutil.open_bfdirstate(ui, repo)
    s = bfdirstate.status(match_.always(repo.root, repo.getcwd()), [], False, False, False)
    (unsure, modified, added, removed, missing, unknown, ignored, clean) = s

    # Need to lock between the standins getting updated and their bfiles getting updated
    wlock = repo.wlock()
    try:
        if opts['check']:
            mod = len(modified) > 0
            for bfile in unsure:
                standin = bfutil.standin(bfile)
                if repo['.'][standin].data().strip() != bfutil.hashfile(repo.wjoin(bfile)):
                    mod = True
                else:
                    bfdirstate.normal(bfutil.unixpath(bfile))
            bfdirstate.write()
            if mod:
                raise util.Abort(_('uncommitted local changes'))
        # XXX handle removed differently
        if not opts['clean']:
            for bfile in unsure + modified + added:
                bfutil.update_standin(repo, bfutil.standin(bfile))
    finally:
        wlock.release()
    return orig(ui, repo, *pats, **opts)

# Override filemerge to prompt the user about how they wish to merge bfiles.
# This will handle identical edits, and copy/rename + edit without prompting the user.
def override_filemerge(origfn, repo, mynode, orig, fcd, fco, fca):
    # Use better variable names here. Because this is a wrapper we cannot change
    # the variable names in the function declaration.
    fcdest, fcother, fcancestor = fcd, fco, fca
    if not bfutil.is_standin(orig):
        return origfn(repo, mynode, orig, fcdest, fcother, fcancestor)
    else:
        if not fcother.cmp(fcdest): # files identical?
            return None

        if fcancestor == fcother: # backwards, use working dir parent as ancestor
            fcancestor = fcdest.parents()[0]

        if orig != fcother.path():
            repo.ui.status(_('merging %s and %s to %s\n')
                    % (bfutil.split_standin(orig), bfutil.split_standin(fcother.path()), bfutil.split_standin(fcdest.path())))
        else:
            repo.ui.status(_('merging %s\n') % bfutil.split_standin(fcdest.path()))

        if fcancestor.path() != fcother.path() and fcother.data() == fcancestor.data():
            return 0
        if fcancestor.path() != fcdest.path() and fcdest.data() == fcancestor.data():
            repo.wwrite(fcdest.path(), fcother.data(), fcother.flags())
            return 0

        if repo.ui.promptchoice(_('bfile %s has a merge conflict\n'
                             'keep (l)ocal or take (o)ther?') % bfutil.split_standin(orig),
                             (_('&Local'), _('&Other')), 0) == 0:
            return 0
        else:
            repo.wwrite(fcdest.path(), fcother.data(), fcother.flags())
            return 0

# Copy first changes the matchers to match standins instead of bfiles.
# Then it overrides util.copyfile in that function it checks if the destination
# bfile already exists. It also keeps a list of copied files so that the bfiles
# can be copied and the dirstate updated.
def override_copy(orig, ui, repo, pats, opts, rename=False):
    # doesn't remove bfile on rename
    if len(pats) < 2:
        # this isn't legal, let the original function deal with it
        return orig(ui, repo, pats, opts, rename)

    def makestandin(relpath):
        return os.path.join(os.path.relpath('.', repo.getcwd()), bfutil.standin(util.canonpath(repo.root, repo.getcwd(), relpath)))

    fullpats = cmdutil.expandpats(pats)
    dest = fullpats[-1]

    if os.path.isdir(dest):
        if not os.path.isdir(makestandin(dest)):
            os.makedirs(makestandin(dest))
    # This could copy both bfiles and normal files in one command, but we don't want
    # to do that first replace their matcher to only match normal files and run it
    # then replace it to just match bfiles and run it again
    nonormalfiles = False
    nobfiles = False
    oldmatch = cmdutil.match
    try:
        manifest = repo[None].manifest()
        def override_match(repo, pats=[], opts={}, globbed=False, default='relpath'):
            match = oldmatch(repo, pats, opts, globbed, default)
            m = copy.copy(match)
            notbfile = lambda f: not bfutil.is_standin(f) and bfutil.standin(f) not in manifest
            m._files = [f for f in m._files if notbfile(f)]
            m._fmap = set(m._files)
            orig_matchfn = m.matchfn
            m.matchfn = lambda f: notbfile(f) and orig_matchfn(f) or None
            return m
        cmdutil.match = override_match
        result = orig(ui, repo, pats, opts, rename)
    except util.Abort as e:
        if str(e) != 'no files to copy':
            raise e
        else:
            nonormalfiles = True
        result = 0
    finally:
        cmdutil.match = oldmatch

    # The first rename can cause our current working directory to be removed. In that case
    # there is nothing left to copy/rename so just quit.
    try:
        repo.getcwd()
    except OSError:
        return result

    try:
        # When we call orig below it creates the standins but we don't add them to the dir state
        # until later so lock during that time.
        wlock = repo.wlock()

        manifest = repo[None].manifest()
        def override_match(repo, pats=[], opts={}, globbed=False, default='relpath'):
            newpats = []
            # The patterns were previously mangled to add .hgbfiles, we need to remove that now
            for pat in pats:
                if match_.patkind(pat) == None and bfutil.short_name in pat:
                    newpats.append(pat.replace( bfutil.short_name, ''))
                else:
                    newpats.append(pat)
            match = oldmatch(repo, newpats, opts, globbed, default)
            m = copy.copy(match)
            bfile = lambda f: bfutil.standin(f) in manifest
            m._files = [bfutil.standin(f) for f in m._files if bfile(f)]
            m._fmap = set(m._files)
            orig_matchfn = m.matchfn
            m.matchfn = lambda f: bfutil.is_standin(f) and bfile(bfutil.split_standin(f)) and orig_matchfn(bfutil.split_standin(f)) or None
            return m
        cmdutil.match = override_match
        listpats = []
        for pat in pats:
            if match_.patkind(pat) != None:
                listpats.append(pat)
            else:
                listpats.append(makestandin(pat))

        try:
            origcopyfile = util.copyfile
            copiedfiles = []
            def override_copyfile(src, dest):
                if bfutil.short_name in src and bfutil.short_name in dest:
                    destbfile = dest.replace(bfutil.short_name, '')
                    if not opts['force'] and os.path.exists(destbfile):
                        raise IOError('', _('destination bfile already exists'))
                copiedfiles.append((src, dest))
                origcopyfile(src, dest)

            util.copyfile = override_copyfile
            result += orig(ui, repo, listpats, opts, rename)
        finally:
            util.copyfile = origcopyfile

        bfdirstate = bfutil.open_bfdirstate(ui, repo)
        for (src, dest) in copiedfiles:
            if bfutil.short_name in src and bfutil.short_name in dest:
                srcbfile = src.replace(bfutil.short_name, '')
                destbfile = dest.replace(bfutil.short_name, '')
                destbfiledir = os.path.dirname(destbfile) or '.'
                if not os.path.isdir(destbfiledir):
                    os.makedirs(destbfiledir)
                if rename:
                    os.rename(srcbfile, destbfile)
                    bfdirstate.remove(bfutil.unixpath(os.path.relpath(srcbfile, repo.root)))
                else:
                    util.copyfile(srcbfile, destbfile)
                bfdirstate.add(bfutil.unixpath(os.path.relpath(destbfile, repo.root)))
        bfdirstate.write()
    except util.Abort as e:
        if str(e) != 'no files to copy':
            raise e
        else:
            nobfiles = True
    finally:
        cmdutil.match = oldmatch
        wlock.release()

    if nobfiles and nonormalfiles:
        raise util.Abort(_('no files to copy'))

    return result

# Standins are only updated (to match the hash of bfiles) before commits.
# Update the standins then run the original revert (changing the matcher to hit standins
# instead of bfiles). Based on the resulting standins update the bfiles. Then return the
# standins to their proper state
def override_revert(orig, ui, repo, *pats, **opts):
    # Because we put the standins in a bad state (by updating them) and then return them
    # to a correct state we need to lock to prevent others from changing them in their
    # incorrect state.
    wlock = repo.wlock()
    try:
        bfdirstate = bfutil.open_bfdirstate(ui, repo)
        (modified, added, removed, missing, unknown, ignored, clean) = bfutil.bfdirstate_status(bfdirstate, repo, repo['.'].rev())
        for bfile in modified:
            bfutil.update_standin(repo, bfutil.standin(bfile))

        oldmatch = cmdutil.match
        try:
            ctx = repo[opts.get('rev')]
            def override_match(repo, pats=[], opts={}, globbed=False, default='relpath'):
                match = oldmatch(repo, pats, opts, globbed, default)
                m = copy.copy(match)
                def tostandin(f):
                    if bfutil.standin(f) in repo[None] or bfutil.standin(f) in ctx:
                        return bfutil.standin(f)
                    return f
                m._files = [tostandin(f) for f in m._files]
                m._fmap = set(m._files)
                orig_matchfn = m.matchfn
                def matchfn(f):
                    if bfutil.is_standin(f):
                        return orig_matchfn(bfutil.split_standin(f)) and (f in repo[None] or f in ctx)
                    return orig_matchfn(f)
                m.matchfn = matchfn
                return m
            cmdutil.match = override_match
            orig(ui, repo, *pats, **opts)
        finally:
            cmdutil.match = oldmatch
        bfcommands.revert_bfiles(ui, repo)
        for bfile in modified:
            if os.path.exists(repo.wjoin(bfutil.standin(bfile))) and bfile in repo['.']:
                bfutil.write_standin(repo, bfutil.standin(bfile), repo['.'][bfile].data().strip(), 'x' in repo['.'][bfile].flags())
    finally:
        wlock.release()

def hg_update(orig, repo, node):
    result = orig(repo, node)
    # XXX check if it worked first
    bfcommands.update_bfiles(repo.ui, repo)
    return result

def hg_clean(orig, repo, node, show_stats=True):
    result = orig(repo, node, show_stats)
    bfcommands.update_bfiles(repo.ui, repo)
    return result

def hg_merge(orig, repo, node, force=None, remind=True):
    result = orig(repo, node, force, remind)
    bfcommands.update_bfiles(repo.ui, repo)
    return result

# When we rebase a repository with remotely changed bfiles, we need
# to explicitly do a clean update so that the entries in .kbf are
# udpated and the new bfiles are pulled
def override_pull(orig, ui, repo, source="default", **opts):
    result = orig(ui, repo, source, **opts)
    if opts.get('rebase', False):
        commands.update(repo.ui, repo, clean = True)
    return result

def override_archive(orig, repo, dest, node, kind, decode=True, matchfn=None,
            prefix=None, mtime=None, subrepos=None):
    # No need to lock because we are only reading history and bfile caches
    # neither of which are modified

    if kind not in archival.archivers:
        raise util.Abort(_("unknown archive type '%s'") % kind)

    ctx = repo[node]

    # In Mercurial <= 1.5 the prefix is passed to the archiver so try that
    # if that doesn't work we are probably in Mercurial >= 1.6 where the
    # prefix is not handled by the archiver
    try:
        archiver = archival.archivers[kind](dest, prefix, mtime or ctx.date()[0])

        def write(name, mode, islink, getdata):
            if matchfn and not matchfn(name):
                return
            data = getdata()
            if decode:
                data = repo.wwritedata(name, data)
            archiver.addfile(name, mode, islink, data)
    except TypeError:
        if kind == 'files':
            if prefix:
                raise util.Abort(_('cannot give prefix when archiving to files'))
        else:
            prefix = archival.tidyprefix(dest, kind, prefix)

        def write(name, mode, islink, getdata):
            if matchfn and not matchfn(name):
                return
            data = getdata()
            if decode:
                data = repo.wwritedata(name, data)
            archiver.addfile(prefix + name, mode, islink, data)

        archiver = archival.archivers[kind](dest, mtime or ctx.date()[0])

    if repo.ui.configbool("ui", "archivemeta", True):
        def metadata():
            base = 'repo: %s\nnode: %s\nbranch: %s\n' % (
                hex(repo.changelog.node(0)), hex(node), ctx.branch())

            tags = ''.join('tag: %s\n' % t for t in ctx.tags()
                           if repo.tagtype(t) == 'global')
            if not tags:
                repo.ui.pushbuffer()
                opts = {'template': '{latesttag}\n{latesttagdistance}',
                        'style': '', 'patch': None, 'git': None}
                cmdutil.show_changeset(repo.ui, repo, opts).show(ctx)
                ltags, dist = repo.ui.popbuffer().split('\n')
                tags = ''.join('latesttag: %s\n' % t for t in ltags.split(':'))
                tags += 'latesttagdistance: %s\n' % dist

            return base + tags

        write('.hg_archival.txt', 0644, False, metadata)

    for f in ctx:
        ff = ctx.flags(f)
        getdata = ctx[f].data
        if bfutil.is_standin(f):
            path = bfutil.find_file(repo, getdata().strip())
            ### TODO: What if the file is not cached?
            f = bfutil.split_standin(f)

            def getdatafn():
                with open(path, 'rb') as fd:
                    return fd.read()

            getdata = getdatafn
        write(f, 'x' in ff and 0755 or 0644, 'l' in ff, getdata)

    if subrepos:
        for subpath in ctx.substate:
            sub = ctx.sub(subpath)
            try:
                sub.archive(repo.ui, archiver, prefix)
            except TypeError:
                sub.archive(archiver, prefix)

    archiver.done()

# If a bfile is modified the change is not reflected in its standin until a commit.
# cmdutil.bail_if_changed raises an exception if the repo has uncommitted changes.
# Wrap it to also check if bfiles were changed. This is used by bisect and backout.
def override_bail_if_changed(orig, repo):
    orig(repo)
    repo.bfstatus = True
    modified, added, removed, deleted = repo.status()[:4]
    repo.bfstatus = False
    if modified or added or removed or deleted:
        raise util.Abort(_('outstanding uncommitted changes'))

# Fetch doesn't use cmdutil.bail_if_changed so override it to add the check
def override_fetch(orig, ui, repo, *pats, **opts):
    repo.bfstatus = True
    modified, added, removed, deleted = repo.status()[:4]
    repo.bfstatus = False
    if modified or added or removed or deleted:
        raise util.Abort(_('outstanding uncommitted changes'))
    return orig(ui, repo, *pats, **opts)

def override_forget(orig, ui, repo, *pats, **opts):
    wctx = repo[None].manifest()
    oldmatch = cmdutil.match
    def override_match(repo, pats=[], opts={}, globbed=False, default='relpath'):
        match = oldmatch(repo, pats, opts, globbed, default)
        m = copy.copy(match)
        notbfile = lambda f: not bfutil.is_standin(f) and bfutil.standin(f) not in wctx
        m._files = [f for f in m._files if notbfile(f)]
        m._fmap = set(m._files)
        orig_matchfn = m.matchfn
        m.matchfn = lambda f: orig_matchfn(f) and notbfile(f)
        return m
    cmdutil.match = override_match
    orig(ui, repo, *pats, **opts)
    cmdutil.match = oldmatch

    m = cmdutil.match(repo, pats, opts)
    try:
        repo.bfstatus = True
        s = repo.status(match=m, clean=True)
    finally:
        repo.bfstatus = False
    forget = sorted(s[0] + s[1] + s[3] + s[6])
    forget = [f for f in forget if bfutil.standin(f) in wctx]

    for f in forget:
        if bfutil.standin(f) not in repo.dirstate and not os.path.isdir(m.rel(bfutil.standin(f))):
            ui.warn(_('not removing %s: file is already untracked\n')
                    % m.rel(f))

    for f in forget:
        if ui.verbose or not m.exact(f):
            ui.status(_('removing %s\n') % m.rel(f))

    # Need to lock because standin files are deleted then removed from the repository
    # and we could race inbetween.
    wlock = repo.wlock()
    try:
        bfdirstate = bfutil.open_bfdirstate(ui, repo)
        for f in forget:
            bfdirstate.remove(bfutil.unixpath(f))
        bfdirstate.write()
        bfutil.repo_remove(repo, [bfutil.standin(f) for f in forget], unlink=True)
    finally:
        wlock.release()

def get_outgoing_bfiles(ui, repo, dest=None, **opts):
    dest = ui.expandpath(dest or 'default-push', dest or 'default')
    dest, branches = hg.parseurl(dest, opts.get('branch'))
    revs, checkout = hg.addbranchrevs(repo, repo, branches, opts.get('rev'))
    if revs:
        revs = [repo.lookup(rev) for rev in revs]

    # Mercurial <= 1.5 had remoteui in cmdutil, then it moved to hg
    try:
        remoteui = cmdutil.remoteui
    except AttributeError:
        remoteui = hg.remoteui

    try:
        remote = hg.repository(remoteui(repo, opts), dest)
    except error.RepoError:
        return None
    o = bfutil.findoutgoing(repo, remote, False)
    if not o:
        return None
    o = repo.changelog.nodesbetween(o, revs)[0]
    if opts.get('newest_first'):
        o.reverse()

    toupload = set()
    for n in o:
        parents = [p for p in repo.changelog.parents(n) if p != node.nullid]
        ctx = repo[n]
        files = set(ctx.files())
        if len(parents) == 2:
            mc = ctx.manifest()
            mp1 = ctx.parents()[0].manifest()
            mp2 = ctx.parents()[1].manifest()
            for f in mp1:
                if f not in mc:
                        files.add(f)
            for f in mp2:
                if f not in mc:
                    files.add(f)
            for f in mc:
                if mc[f] != mp1.get(f, None) or mc[f] != mp2.get(f, None):
                    files.add(f)
        toupload = toupload.union(set([f for f in files if bfutil.is_standin(f) and f in ctx]))
    return toupload

def override_outgoing(orig, ui, repo, dest=None, **opts):
    orig(ui, repo, dest, **opts)

    if opts.pop('bf', None):
        toupload = get_outgoing_bfiles(ui, repo, dest, **opts)
        if toupload is None:
            ui.status(_('kbfiles: No remote repo\n'))
        else:
            ui.status(_('kbfiles to upload:\n'))
            for file in toupload:
                ui.status(bfutil.split_standin(file) + '\n')
            ui.status('\n')

def override_summary(orig, ui, repo, *pats, **opts):
    orig(ui, repo, *pats, **opts)

    if opts.pop('bf', None):
        toupload = get_outgoing_bfiles(ui, repo, None, **opts)
        if toupload is None:
            ui.status(_('kbfiles: No remote repo\n'))
        else:
            ui.status(_('kbfiles: %d to upload\n') % len(toupload))

def override_addremove(orig, ui, repo, *pats, **opts):
    # Check if the parent or child has bfiles if they do don't allow it.
    # If there is a symlink in the manifest then getting the manifest throws an exception
    # catch it and let addremove deal with it. This happens in Mercurial's test
    # test-addremove-symlink
    try:
        manifesttip = set(repo['tip'].manifest())
    except util.Abort:
        manifesttip = set()
    try:
        manifestworking = set(repo[None].manifest())
    except util.Abort:
        manifestworking = set()

    # Manifests are only iterable so turn them into sets then union
    for file in manifesttip.union(manifestworking):
        if file.startswith(bfutil.short_name):
            raise util.Abort(_('addremove cannot be run on a repo with bfiles'))

    return orig(ui, repo, *pats, **opts)

def uisetup(ui):
    # Disable auto-status for some commands which assume that all
    # files in the result are under Mercurial's control

    entry = extensions.wrapcommand(commands.table, 'add', override_add)
    addopt = [('', 'bf', None, _('add as bfile')),
            ('', 'bfsize', '', _('add all files above this size (in megabytes) as bfiles (default: 10)'))]
    entry[1].extend(addopt)

    entry = extensions.wrapcommand(commands.table, 'addremove', override_addremove)
    entry = extensions.wrapcommand(commands.table, 'remove', override_remove)
    entry = extensions.wrapcommand(commands.table, 'forget', override_forget)
    entry = extensions.wrapcommand(commands.table, 'status', override_status)

    entry = extensions.wrapcommand(commands.table, 'verify', override_verify)
    verifyopt = [('', 'bf', None, _('verify bfiles')),
                 ('', 'bfa', None, _('verify all revisions of bfiles not just current')),
                 ('', 'bfc', None, _('verify bfile contents not just existence'))]
    entry[1].extend(verifyopt)

    entry = extensions.wrapcommand(commands.table, 'outgoing', override_outgoing)
    outgoingopt = [('', 'bf', None, _('display outgoing bfiles'))]
    entry[1].extend(outgoingopt)
    entry = extensions.wrapcommand(commands.table, 'summary', override_summary)
    summaryopt = [('', 'bf', None, _('display outgoing bfiles'))]
    entry[1].extend(summaryopt)

    entry = extensions.wrapcommand(commands.table, 'update', override_update)
    entry = extensions.wrapcommand(commands.table, 'pull', override_pull)
    entry = extensions.wrapfunction(filemerge, 'filemerge', override_filemerge)
    entry = extensions.wrapfunction(cmdutil, 'copy', override_copy)

    # Backout calls revert so we need to override both the command and the function
    entry = extensions.wrapcommand(commands.table, 'revert', override_revert)
    entry = extensions.wrapfunction(commands, 'revert', override_revert)

    # clone uses hg._update instead of hg.update even though they are the
    # same function... so wrap both of them)
    extensions.wrapfunction(hg, 'update', hg_update)
    extensions.wrapfunction(hg, '_update', hg_update)
    extensions.wrapfunction(hg, 'clean', hg_clean)
    extensions.wrapfunction(hg, 'merge', hg_merge)

    extensions.wrapfunction(archival, 'archive', override_archive)
    extensions.wrapfunction(cmdutil, 'bail_if_changed', override_bail_if_changed)

    for name, module in extensions.extensions():
        if name == 'fetch':
            extensions.wrapcommand(getattr(module, 'cmdtable'), 'fetch', override_fetch)


