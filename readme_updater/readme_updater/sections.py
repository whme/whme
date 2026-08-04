"""The generated README sections and the template that places them.

The README is the template: it declares ``<!-- name:start -->`` /
``<!-- name:end -->`` markers wherever it wants a generated block. This
module fills those markers and never decides their order — swapping two
sections is an edit to the README, not to the code.

:class:`Sections` is the typed set of blocks the code knows how to
produce; :func:`consistency_errors` keeps it and the README in step.
"""
