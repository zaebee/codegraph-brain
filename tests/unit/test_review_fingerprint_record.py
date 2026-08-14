"""A review record states which reviewer produced it, and how it knows."""

from cgis.guardian.martian import ReviewRecord


def test_record_carries_a_fingerprint_and_its_provenance(sample_record: ReviewRecord) -> None:
    assert sample_record.review_fingerprint
    assert sample_record.review_fingerprint_source in {"measured", "reconstructed"}


def test_provenance_has_no_default() -> None:
    """A reconstructed row must never be able to claim it was measured.

    The two do not carry the same guarantee: a reconstructed digest is rebuilt
    from git and cannot see an uncommitted edit, which is exactly the blindness
    the measured one exists to escape.
    """
    fields = ReviewRecord.model_fields
    assert fields["review_fingerprint_source"].is_required()
    assert fields["review_fingerprint"].is_required()


def test_provider_is_stated_not_inferred() -> None:
    """The producer knows; a model-name prefix is a guess that breaks."""
    assert ReviewRecord.model_fields["finder_provider"].is_required()
