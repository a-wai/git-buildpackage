# vim: set fileencoding=utf-8 :
#
# (C) 2011 Guido Guenther <agx@sigxcpu.org>
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
"""
Someone who modifiers something in git

like committing changes or authoring a patch
"""

from gbp.git.errors import GitError


class GitModifierError(GitError):
    """Exception thrown by L{GitModifier}"""
    pass


class GitModifier(object):
    """Stores authorship/comitter information"""
    def __init__(self, name=None, email=None, date=None):
        self.name = name
        self.email = email
        self.date = date

    def _get_env(self, who):
        """Get author or comitter information as env var dictionary"""
        who = who.upper()
        if who not in ['AUTHOR', 'COMMITTER']:
            raise GitModifierError("Neither comitter nor author")

        extra_env = {}
        if self.name:
            extra_env['GIT_%s_NAME' % who] = self.name
        if self.email:
            extra_env['GIT_%s_EMAIL' % who] = self.email
        if self.date:
            extra_env['GIT_%s_DATE' % who] = self.date
        return extra_env

    def get_author_env(self):
        """
        Get env vars for authorship information

        >>> g = GitModifier("foo", "bar")
        >>> g.get_author_env()
        {'GIT_AUTHOR_EMAIL': 'bar', 'GIT_AUTHOR_NAME': 'foo'}

        @return: Author information suitable to use as environment variables
        @rtype: C{dict}
        """
        return self._get_env('author')

    def get_committer_env(self):
        """
        Get env vars for comitter information

        >>> g = GitModifier("foo", "bar")
        >>> g.get_committer_env()
        {'GIT_COMMITTER_NAME': 'foo', 'GIT_COMMITTER_EMAIL': 'bar'}

        @return: Commiter information suitable to use as environment variables
        @rtype: C{dict}
        """
        return self._get_env('committer')

