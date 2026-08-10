"""Club rules expressed as SQL over ``customer_profile``, for callers that filter in the database.

Why this module exists at all, rather than living beside its Python twin in :mod:`src.loyalty.lottery`:
it was carved out under duress. ``lottery`` imports :mod:`src.auth.service` for one call, and back
then that dragged a package cycle through ``src/auth/__init__.py`` into a half-built
``src.auth.dependencies`` -- so pointing the CRM router at ``lottery`` turned
``import src.crm.router`` from working into an ImportError.

**That cycle is gone** (``src/users/__init__.py`` stopped re-exporting the users router; guarded by
``tests/test_modules_import_standalone.py``), so the original reason no longer applies and this
module could technically be folded back. It stays because the reason that outlived the cycle is the
better one: these predicates need nothing but the model, and a caller that only wants to filter on
a club rule has no business importing the draw -- with its snapshots, its freeze and its
``src.auth`` dependency -- to get one WHERE clause.

Nothing here restates a rule. Each function is the single SQL form of a predicate whose Python
original lives in :mod:`src.loyalty.lottery`, and ``tests/test_detector_agreement.py`` binds the two
formulations together.
"""

from sqlalchemy import ColumnElement, and_

from src.loyalty.models import CustomerProfile


def survey_complete_sql() -> ColumnElement[bool]:
    """:func:`~src.loyalty.lottery.has_survey`, as SQL -- gender AND city, no 18+ half.

    Two callers need exactly this much: :func:`~src.loyalty.lottery.eligible_sql` builds on it, and
    the CRM list of members who earned points and never finished the survey filters on its negation.
    That second caller wants no adulthood check -- a reminder is due regardless of age -- which is
    why the survey question exists apart from the eligibility question it used to be welded into.

    The opposite is written ``not_(survey_complete_sql())``, never a hand-rolled
    ``gender IS NULL OR city IS NULL``: ``IS NOT NULL`` never evaluates to NULL, so the negation is
    total, and a derived one cannot drift from this definition the way a twin would.
    """
    return and_(
        CustomerProfile.gender.is_not(None),
        CustomerProfile.city.is_not(None),
    )
