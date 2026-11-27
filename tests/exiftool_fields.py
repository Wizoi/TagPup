"""The Fields a test hands a service, closed when the test ends.

A service takes the operation's Fields (tagpup.files.field_values.opened) and never closes one it was handed: its caller
does, in a `with` block. A test that hands one straight to a call (`file_changes.settle(library, field_values.opened(X))`)
closes nothing: Fields has no __del__, so its session's exit never runs. Under the real ExifTool the process is ended when
pyexiftool's helper is collected, but under the stand-in (tests/fake_exiftool.py) the count of open sessions stays above
0, and the patches it holds while one is open (field_values.PATCH_IN_PLACE off, the picture check passed by) stay on for
every later test of the process: a test of a patch in place run after one that leaked fails. `opened(self, X)` is that
call's Fields, closed by the test's cleanups; tests/test_tests_have_homes_of_their_own.py refuses an inline
`field_values.opened(` anywhere else in tests/.
"""
from tagpup.files import field_values


def opened(testcase, exiftool_path):
    """A Fields over `exiftool_path` for one call of `testcase`, closed when the test ends (its cleanups, before those
    its setUp registered: the stand-in's patch of ExifToolSession is still on as it closes)."""
    fields = field_values.opened(exiftool_path)
    testcase.addCleanup(fields.close)
    return fields
