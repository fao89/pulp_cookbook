# (C) Copyright 2018 Simon Baatz <gmbnomis@gmail.com>
#
# SPDX-License-Identifier: GPL-2.0-or-later

import logging
import os

from gettext import gettext as _

from django.core.files import File

from pulpcore.plugin.models import (
    ProgressReport,
    PublishedArtifact,
    PublishedMetadata,
    RepositoryVersion,
)
from pulpcore.plugin.tasking import WorkingDirectory

from pulp_cookbook.app.models import CookbookPackageContent, CookbookPublication
from pulp_cookbook.metadata import Entry, Universe

log = logging.getLogger(__name__)

BASE_PATH_MARKER = "{{ base_path }}"
BATCH_SIZE = 2000  # Number of objects to query/save


def path_template(artifact_path):
    """Artifact path to write into published '__universe__' file."""
    return BASE_PATH_MARKER + "/" + artifact_path


def replace_all_paths(content, base_url):
    """Replace BASE_PATH_MARKERs with base_url in the give '__universe__' content."""
    return content.replace(BASE_PATH_MARKER, base_url)


def publish(repository_version_pk):
    """
    Create a Publication based on a RepositoryVersion.

    Args:
        repository_version_pk (str): Create a publication from this repository version.

    """
    repository_version = RepositoryVersion.objects.get(pk=repository_version_pk)

    log.info(
        _("Publishing: repository=%(repository)s, version=%(version)d"),
        {"repository": repository_version.repository.name, "version": repository_version.number},
    )

    with WorkingDirectory():
        with CookbookPublication.create(repository_version) as publication:
            universe = Universe("__universe__")
            universe.write(populate(publication))
            PublishedMetadata.create_from_file(
                relative_path=os.path.basename(universe.relative_path),
                publication=publication,
                file=File(open(universe.relative_path, "rb")),
            )

    log.info(_("Publication: %(publication)s created"), {"publication": publication.pk})


def batch_qs(qs, batch_size=1000):
    """
    Returns a queryset batch in the given queryset.

    Usage:
        # Make sure to order your querset
        article_qs = Article.objects.order_by('id')
        for qs in batch_qs(article_qs):
            for article in qs:
                print article.body
    """
    total = qs.count()
    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        yield qs[start:end]


def populate(publication, batch_size=BATCH_SIZE):
    """
    Populate a publication.

    Create published artifacts and yield a Universe Entry for each.

    Args:
        publication (pulpcore.plugin.models.Publication): A Publication to populate.

    Yields:
        Entry: Universe entry for each cookbook

    """
    content_ca_qs = (
        CookbookPackageContent.objects.filter(pk__in=publication.repository_version.content)
        .order_by("-pulp_created", "pk")
        .prefetch_related("contentartifact_set")
    )

    with ProgressReport(message="Publishing Content", code="publishing.content") as pb:
        for content_slice_qs in batch_qs(content_ca_qs, batch_size=batch_size):
            published_artifacts = []
            for content in content_slice_qs:
                relative_path = "cookbook_files/{}/{}/".format(
                    content.name, content.version.replace(".", "_")
                )
                for content_artifact in content.contentartifact_set.all():
                    art_path = os.path.join(relative_path, content_artifact.relative_path)
                    published_artifacts.append(
                        PublishedArtifact(
                            relative_path=art_path,
                            publication=publication,
                            content_artifact=content_artifact,
                        )
                    )
                    entry = Entry(
                        name=content.name,
                        version=content.version,
                        download_url=path_template(art_path),
                        dependencies=content.dependencies,
                    )
                    yield entry
            PublishedArtifact.objects.bulk_create(published_artifacts)
            pb.increase_by(len(published_artifacts))
