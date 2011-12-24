# vim: set fileencoding=utf-8 :
#
# (C) 2011 Guido Günther <agx@sigxcpu.org>
#    This program is free software; you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation; either version 2 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program; if not, write to the Free Software
#    Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
#
"""manage patches in a patch queue"""

import errno
import re
import os
import shutil
import subprocess
import sys
import tempfile
from gbp.config import (GbpOptionParser, GbpOptionGroup)
from gbp.git import (GitRepositoryError, GitRepository)
from gbp.command_wrappers import (Command, GitCommand, RunAtCommand,
                                  CommandExecFailed)
from gbp.errors import GbpError
import gbp.log
from gbp.patch_series import (PatchSeries, Patch)

PQ_BRANCH_PREFIX = "patch-queue/"
PATCH_DIR = "debian/patches/"
SERIES_FILE = os.path.join(PATCH_DIR,"series")


def is_pq_branch(branch):
    """
    is branch a patch-queue branch?

    >>> is_pq_branch("foo")
    False
    >>> is_pq_branch("patch-queue/foo")
    True
    """
    return [False, True][branch.startswith(PQ_BRANCH_PREFIX)]


def pq_branch_name(branch):
    """
    get the patch queue branch corresponding to branch

    >>> pq_branch_name("patch-queue/master")
    >>> pq_branch_name("foo")
    'patch-queue/foo'
    """
    if not is_pq_branch(branch):
        return PQ_BRANCH_PREFIX + branch


def pq_branch_base(pq_branch):
    """
    get the branch corresponding to the given patch queue branch

    >>> pq_branch_base("patch-queue/master")
    'master'
    >>> pq_branch_base("foo")
    """
    if is_pq_branch(pq_branch):
        return pq_branch[len(PQ_BRANCH_PREFIX):]

def write_patch(patch, options):
    """Write the patch exported by 'git-format-patch' to it's final location
       (as specified in the commit)"""
    oldname = patch[len(PATCH_DIR):]
    newname = oldname
    tmpname = patch + ".gbp"
    old = file(patch, 'r')
    tmp = file(tmpname, 'w')
    in_patch = False
    topic = None

    # Skip first line (From <sha1>)
    old.readline()
    for line in old:
        if line.lower().startswith("gbp-pq-topic: "):
            topic = line.split(" ",1)[1].strip()
            gbp.log.debug("Topic %s found for %s" % (topic, patch))
            continue
        tmp.write(line)
    tmp.close()
    old.close()

    if not options.patch_numbers:
        patch_re = re.compile("[0-9]+-(?P<name>.+)")
        m = patch_re.match(oldname)
        if m:
            newname = m.group('name')

    if topic:
        topicdir = os.path.join(PATCH_DIR, topic)
    else:
        topicdir = PATCH_DIR

    if not os.path.isdir(topicdir):
        os.makedirs(topicdir, 0755)

    os.unlink(patch)
    dstname = os.path.join(topicdir, newname)
    gbp.log.debug("Moving %s to %s" % (tmpname, dstname))
    shutil.move(tmpname, dstname)

    return dstname


def export_patches(repo, branch, options):
    """Export patches from the pq branch into a patch series"""
    if is_pq_branch(branch):
        base = pq_branch_base(branch)
        gbp.log.info("On '%s', switching to '%s'" % (branch, base))
        branch = base
        repo.set_branch(branch)

    pq_branch = pq_branch_name(branch)
    try:
        shutil.rmtree(PATCH_DIR)
    except OSError, (e, msg):
        if e != errno.ENOENT:
            raise GbpError, "Failed to remove patch dir: %s" % msg
        else:
            gbp.log.debug("%s does not exist." % PATCH_DIR)

    patches = repo.format_patches(branch, pq_branch, PATCH_DIR,
                                  signature=False)
    if patches:
        f = file(SERIES_FILE, 'w')
        gbp.log.info("Regenerating patch queue in '%s'." % PATCH_DIR)
        for patch in patches:
            filename = write_patch(patch, options)
            f.write(filename[len(PATCH_DIR):] + '\n')

        f.close()
        GitCommand('status')(['--', PATCH_DIR])
    else:
        gbp.log.info("No patches on '%s' - nothing to do." % pq_branch)


def get_maintainer_from_control():
    """Get the maintainer from the control file"""
    cmd = 'sed -n -e \"s/Maintainer: \\+\\(.*\\)/\\1/p\" debian/control'
    maintainer = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE).stdout.readlines()[0].strip()

    m = re.match('(?P<name>.*[^ ]) *<(?P<email>.*)>', maintainer)
    if m:
        return m.group('name'), m.group('email')
    else:
        return None, None


def safe_patches(series):
    """
    Safe the current patches in a temporary directory
    below .git/

    @param series: path to series file
    @return: tmpdir and path to safed series file
    @rtype: tuple
    """

    src = os.path.dirname(series)
    name = os.path.basename(series)

    tmpdir = tempfile.mkdtemp(dir='.git/', prefix='gbp-pq')
    patches = os.path.join(tmpdir, 'patches')
    series = os.path.join(patches, name)

    gbp.log.debug("Safeing patches '%s' in '%s'" % (src, tmpdir))
    shutil.copytree(src, patches)

    return (tmpdir, series)


def import_quilt_patches(repo, branch, series, tries, force):
    """
    apply a series of quilt patches in the series file 'series' to branch
    the patch-queue branch for 'branch'

    @param repo: git repository to work on
    @param branch: branch to base pqtch queue on
    @param series; series file to read patches from
    @param tries: try that many times to apply the patches going back one
                  commit in the branches history after each failure.
    @param force: import the patch series even if the branch already exists
    """
    tmpdir = None

    if is_pq_branch(branch):
        if force:
            branch = pq_branch_base(branch)
            pq_branch = pq_branch_name(branch)
            repo.checkout(branch)
        else:
            gbp.log.err("Already on a patch-queue branch '%s' - doing nothing." % branch)
            raise GbpError
    else:
        pq_branch = pq_branch_name(branch)

    if repo.has_branch(pq_branch):
        if force:
            drop_pq(repo, branch)
        else:
            raise GbpError, ("Patch queue branch '%s'. already exists. Try 'rebase' instead."
                             % pq_branch)

    commits = repo.get_commits(options=['-%d' % tries], first_parent=True)
    # If we go back in history we have to safe our pq so we always try to apply
    # the latest one
    if len(commits) > 1:
        tmpdir, series = safe_patches(series)

    queue = PatchSeries.read_series_file(series)
    for commit in commits:
        try:
            gbp.log.info("Trying to apply patches at '%s'" % commit)
            repo.create_branch(pq_branch, commit)
        except CommandExecFailed:
            raise GbpError, ("Cannot create patch-queue branch '%s'." % pq_branch)

        repo.set_branch(pq_branch)
        for patch in queue:
            gbp.log.debug("Applying %s" % patch.path)
            try:
                apply_and_commit_patch(repo, patch, patch.topic)
            except (GbpError, GitRepositoryError, CommandExecFailed):
                repo.set_branch(branch)
                repo.delete_branch(pq_branch)
                break
        else:
            # All patches applied successfully
            break
    else:
        raise GbpError, "Couldn't apply patches"

    if tmpdir:
        gbp.log.debug("Remove temporary patch safe '%s'" % tmpdir)
        shutil.rmtree(tmpdir)


def switch_to_pq_branch(repo, branch):
    """Switch to patch-queue branch if not already there, create it if it
       doesn't exist yet"""
    if is_pq_branch (branch):
        return

    pq_branch = pq_branch_name(branch)
    if not repo.has_branch(pq_branch):
        try:
            repo.create_branch(pq_branch)
        except CommandExecFailed:
            raise GbpError, ("Cannot create patch-queue branch '%s'. Try 'rebase' instead."
                % pq_branch)

    gbp.log.info("Switching to '%s'" % pq_branch)
    repo.set_branch(pq_branch)


def apply_single_patch(repo, branch, patch, topic=None):
    switch_to_pq_branch(repo, branch)
    apply_and_commit_patch(repo, patch, topic)


def apply_and_commit_patch(repo, patch, topic=None):
    """apply a single patch 'patch', add topic 'topic' and commit it"""
    author = { 'name': patch.author,
               'email': patch.email,
               'date': patch.date }

    if not (patch.author and patch.email):
        name, email = get_maintainer_from_control()
        if name:
            gbp.log.warn("Patch '%s' has no authorship information, "
                         "using '%s <%s>'" % (patch, name, email))
            author['name'] = name
            author['email'] = email
        else:
            gbp.log.warn("Patch %s has no authorship information")

    repo.apply_patch(patch.path)
    tree = repo.write_tree()
    msg = "%s\n\n%s" % (patch.subject, patch.long_desc)
    if topic:
        msg += "\nGbp-Pq-Topic: %s" % topic
    commit = repo.commit_tree(tree, msg, [repo.head], author=author)
    repo.update_ref('HEAD', commit, msg="gbp-pq import %s" % patch.path)


def drop_pq(repo, branch):
    if is_pq_branch(branch):
        gbp.log.err("On a patch-queue branch, can't drop it.")
        raise GbpError
    else:
        pq_branch = pq_branch_name(branch)

    if repo.has_branch(pq_branch):
        repo.delete_branch(pq_branch)
        gbp.log.info("Dropped branch '%s'." % pq_branch)
    else:
        gbp.log.info("No patch queue branch found - doing nothing.")


def rebase_pq(repo, branch):
    if is_pq_branch(branch):
        base = pq_branch_base(branch)
    else:
        switch_to_pq_branch(repo, branch)
        base = branch
    GitCommand("rebase")([base])


def main(argv):
    retval = 0

    parser = GbpOptionParser(command=os.path.basename(argv[0]), prefix='',
                             usage="%prog [options] action - maintain patches on a patch queue branch\n"
        "Actions:\n"
        "  export         export the patch queue associated to the current branch\n"
        "                 into a quilt patch series in debian/patches/ and update the\n"
        "                 series file.\n"
        "  import         create a patch queue branch from quilt patches in debian/patches.\n"
        "  rebase         switch to patch queue branch associated to the current\n"
        "                 branch and rebase against current branch.\n"
        "  drop           drop (delete) the patch queue associated to the current branch.\n"
        "  apply          apply a patch\n")
    parser.add_boolean_config_file_option(option_name="patch-numbers", dest="patch_numbers")
    parser.add_option("-v", "--verbose", action="store_true", dest="verbose", default=False,
                      help="verbose command execution")
    parser.add_option("--topic", dest="topic", help="in case of 'apply' topic (subdir) to put patch into")
    parser.add_config_file_option(option_name="time-machine", dest="time_machine", type="int")
    parser.add_option("--force", dest="force", action="store_true", default=False,
                      help="in case of import even import if the branch already exists")
    parser.add_config_file_option(option_name="color", dest="color", type='tristate')

    (options, args) = parser.parse_args(argv)
    gbp.log.setup(options.color, options.verbose)

    if len(args) < 2:
        gbp.log.err("No action given.")
        return 1
    else:
        action = args[1]

    if args[1] in ["export", "import", "rebase", "drop"]:
        pass
    elif args[1] in ["apply"]:
        if len(args) != 3:
            gbp.log.err("No patch name given.")
            return 1
        else:
            patchfile = args[2]
    else:
        gbp.log.err("Unknown action '%s'." % args[1])
        return 1

    try:
        repo = GitRepository(os.path.curdir)
    except GitRepositoryError:
        gbp.log.err("%s is not a git repository" % (os.path.abspath('.')))
        return 1

    try:
        current = repo.get_branch()
        if action == "export":
            export_patches(repo, current, options)
        elif action == "import":
            series = SERIES_FILE
            tries = options.time_machine if (options.time_machine > 0) else 1
            import_quilt_patches(repo, current, series, tries, options.force)
            current = repo.get_branch()
            gbp.log.info("Patches listed in '%s' imported on '%s'" %
                          (series, current))
        elif action == "drop":
            drop_pq(repo, current)
        elif action == "rebase":
            rebase_pq(repo, current)
        elif action == "apply":
            patch = Patch(patchfile)
            apply_single_patch(repo, current, patch, options.topic)
    except CommandExecFailed:
        retval = 1
    except GbpError, err:
        if len(err.__str__()):
            gbp.log.err(err)
        retval = 1

    return retval

if __name__ == '__main__':
    sys.exit(main(sys.argv))
