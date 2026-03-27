# SPDX-FileCopyrightText: (C) 2025 Rivos Inc.
# SPDX-FileCopyrightText: Copyright 2024 Google LLC
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
# SPDX-License-Identifier: Apache-2.0

import os
import subprocess
import sys
import tempfile

# Version selection for checkout and cat_file
VERSION_BASE = 1
VERSION_OURS = 2
VERSION_THEIRS = 3
VERSIONS = (VERSION_BASE, VERSION_OURS, VERSION_THEIRS)

# File state information
STATE_MODIFIED = "U"
STATE_ADDED = "A"
STATE_DELETED = "D"


def get_conflicts():
  """Returns a list of conflicting files for the current repo.
  Each file is returned as a tuple: (file, state), with state being two
  characters (ours and theirs) from one of the STATE_ states.
  """
  conflicts = iter(
    subprocess.check_output(["git", "status", "-z"], cwd=repo_path()).split(
      b"\0"
    )
  )
  for path in conflicts:
    path = path.decode()
    # If it's a rename, consume an extra file
    state = path[0:2]
    if "R" in state:
      next(conflicts)
      continue
    if "U" in state or state in ("AA", "DD"):
      yield (path[3:], state)


def clone_tmp(commit=None):
  """Does a shared clone into a temporary directory. No checkout is performed
  unless a commit is specified for checkout.
  Returns the temporary directory. You are responsible for deleting it."""
  tempdir = tempfile.mkdtemp()
  subprocess.check_call(["git", "clone", "-qns", ".", tempdir])
  if commit:
    subprocess.check_call(["git", "checkout", "-q", commit], cwd=tempdir)
  return tempdir


def checkout(path, version):
  """Runs git checkout on a relative path, with either --ours or --theirs."""
  if version == VERSION_BASE:
    return False
  return (
    subprocess.call(
      [
        "git",
        "checkout",
        "-q",
        "--ours" if version == VERSION_OURS else "--theirs",
        "--",
        path,
      ],
      cwd=repo_path(),
    )
    == 0
  )


def cat(path, ref=":", relative=False, quiet=False):
  """Returns a file for a path (and ref, if provided)."""
  return subprocess.Popen(
    [
      "git",
      "show",
      ":".join(
        (ref, path if not relative or path.startswith("/") else f"./{path}")
      ),
    ],
    cwd=None if relative else repo_path(),
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL if quiet else None,
  ).stdout


def cat_files(path, state=STATE_MODIFIED * 2):
  """Returns a tuple of three files (base, ours, theirs) for a path.
  Substitutes in None if a tree does not contain the file (based on state)
  You can avoid specifying state if you know for a fact all files exist.
  """
  # Maps VERSION to whether the file exists in that index, based on state
  exists = (
    None,
    STATE_ADDED not in state,
    state[0] != STATE_DELETED,
    state[1] != STATE_DELETED,
  )
  return tuple(
    cat(path, f":{version}") if exists[version] else None
    for version in VERSIONS
  )


def add(path):
  """Runs git add on the relative path."""
  return subprocess.call(["git", "add", "--", path], cwd=repo_path()) == 0


def rev_parse(revs, repo=None, quiet=False):
  """Parses a revspec for a list of revisions to be considered."""
  revlist = subprocess.check_output(
    ["git", "rev-parse", "--revs-only", revs],
    universal_newlines=True,
    cwd=repo or None,
    stderr=subprocess.DEVNULL if quiet else None,
  )
  return [rev.lstrip("^") for rev in revlist.splitlines()]


def get_version(repo=None, githash=None):
  """Returns a friendly string of the current version.
  Specify repo to use a git repo other than the current one."""
  # Check for a _version.py file for special projects and kischvidimer itself
  versionpath = os.path.join(repo, "_version.py") if repo else "_version.py"
  try:
    versionfile = open_rb(versionpath, githash, quiet=True)
  except FileNotFoundError:
    versionfile = None
  if versionfile:
    glbls = {
      "__builtins__": {"object": object, "str": str, "__import__": __import__}
    }
    try:
      exec(versionfile.read().decode(), glbls)
    except NameError as e:
      print(
        f"Failed to execute _version.py ('{e.name}' not available)",
        file=sys.stderr,
      )
    if "__version__" in glbls:
      return glbls["__version__"]
  ret = subprocess.run(
    ["git", "describe", "--all", "--always", "--long"]
    + (["--", githash] if githash else ["--broken", "--dirty"]),
    cwd=repo or ".",
    capture_output=True,
    text=True,
  )
  if ret.returncode == 0:
    ver = ret.stdout.strip()
    if ver.startswith("pipelines/") and "CI_COMMIT_REF_NAME" in os.environ:
      ver = f"{os.environ['CI_COMMIT_REF_NAME']}-{ver.partition('-')[2]}"
    if ver.startswith("remotes/"):
      ver = ver[8:].partition("/")[2]
    ver = ver.replace("heads/", "")
    ver = ver.replace("tags/", "")
    return ver
  return "unknown"


# A cache for repo_path
__repopath = None


def repo_path(path=""):
  """Returns the absolute path for a file in the repo."""
  global __repopath
  if __repopath is None:
    __repopath = subprocess.check_output(
      ["git", "rev-parse", "--show-toplevel"], universal_newlines=True
    ).rstrip()
  return os.path.join(os.fsdecode(__repopath), path)


def is_in_repo(path):
  """Return true if the specified path is within the current repo."""
  # We use abspath instead of realpath since we don't support having symlinks in
  # repos (since it breaks under Windows). This would only affect the outcome of
  # this function if someone had a symlink in a project repo pointing to a file
  # or directory outside of the repo.
  real_repo = os.path.abspath(repo_path())
  real_path = os.path.abspath(path)
  try:
    return os.path.commonpath((real_repo, real_path)) == real_repo
  except ValueError:
    return False


def listdir(path_or_tuple, githash=None):
  """Behaves like os.listdir, but if path_or_tuple is a tuple of (path, githash)
  (or if githash is specified separately), will query git instead for non-abs
  paths. For absolute paths, will just query the filesystem.
  """
  if isinstance(path_or_tuple, tuple):
    path_or_tuple, githash = path_or_tuple
  if not githash or not is_in_repo(path_or_tuple):
    return os.listdir(path_or_tuple)
  return [
    os.path.basename(path)
    for path in ls_tree(
      path_or_tuple + "/.", githash, full_tree=False, recurse=False
    )
  ]


def isdir(path_or_tuple, githash=None):
  """Behaves like os.path.isdir and handles git hashes as in listdir."""
  if isinstance(path_or_tuple, tuple):
    path_or_tuple, githash = path_or_tuple
  if not githash or not is_in_repo(path_or_tuple):
    return os.path.isdir(path_or_tuple)
  return bool(
    ls_tree(path_or_tuple + "/.", githash, full_tree=False, recurse=False)
  )


def isfile(path_or_tuple, githash=None):
  """Behaves like os.path.isfile and handles git hashes as in listdir."""
  if isinstance(path_or_tuple, tuple):
    path_or_tuple, githash = path_or_tuple
  if not githash or not is_in_repo(path_or_tuple):
    return os.path.isfile(path_or_tuple)
  if isdir(path_or_tuple, githash):
    return False
  return bool(ls_tree(path_or_tuple, githash, full_tree=False, recurse=False))


def open_rb(path_or_tuple, githash=None, quiet=False):
  """Behaves like open(x, 'rb') and handles git hashes as in listdir."""
  if isinstance(path_or_tuple, tuple):
    path_or_tuple, githash = path_or_tuple
  if not githash or not is_in_repo(path_or_tuple):
    return open(path_or_tuple, "rb")
  return cat(path_or_tuple, githash, relative=True, quiet=quiet)


# A cache for is_rebase
__isrebase = None


def is_rebase():
  """Returns True if the git repo is in the middle of a rebase (vs a merge)."""
  global __isrebase
  if __isrebase is None:
    __isrebase = False
    for d in ("rebase-merge", "rebase-apply"):
      if os.path.isdir(
        subprocess.check_output(
          ["git", "rev-parse", "--git-path", d], universal_newlines=True
        ).rstrip()
      ):
        __isrebase = True
  return __isrebase


# A cache for ls_tree, indexed by (path, commit)
__lscache = {}


def ls_tree(path, commit="HEAD", full_tree=True, recurse=True):
  """Returns a list of all the files under a given path and commit."""
  if (path, commit, full_tree) not in __lscache:
    __lscache[(path, commit, full_tree)] = subprocess.check_output(
      ["git", "ls-tree", "--name-only"]
      + ["-r"] * recurse
      + ["--full-tree"] * full_tree
      + [commit, path],
      cwd=repo_path() if full_tree else None,
      universal_newlines=True,
    ).splitlines()
  return __lscache[(path, commit, full_tree)]


def main(argv):
  for path in argv[1:] or [None]:
    print(get_version(path))
  return 0


if __name__ == "__main__":
  sys.exit(main(sys.argv))
