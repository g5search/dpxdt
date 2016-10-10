#!/usr/bin/env python
# Copyright 2013 Brett Slatkin
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Web-based API for managing screenshots and incremental perceptual diffs.

Lifecycle of a release:

1. User creates a new build, which represents a single product or site that
   will be screenshotted repeatedly over time. This may happen very
   infrequenty through a web UI.

2. User creates a new release candidate for the build with a specific release
   name. The candidate is an attempt at finishing a specific release name. It
   may take many attempts, many candidates, before the release with that name
   is complete and can be marked as good.

3. User creates many runs for the candidate created in #2. Each run is
   identified by a unique name that describes what it does. For example, the
   run name could be the URL path for a page being screenshotted. The user
   associates each run with a new screenshot artifact. Runs are automatically
   associated with a corresponding run from the last good release. This makes
   it easy to compare new and old screenshots for runs with the same name.

4. User uploads a series of screenshot artifacts identified by content hash.
   Perceptual diffs between these new screenshots and the last good release
   may also be uploaded as an optimization. This may happen in parallel
   with #3.

5. The user marks the release candidate as having all of its expected runs
   present, meaning it will no longer receive new runs. This should only
   happen after all screenshot artifacts have finished uploading.

6. If a run indicates a previous screenshot, but no perceptual diff has
   been made to compare the new and old versions, a worker will do a perceptual
   diff, upload it, and associate it with the run.

7. Once all perceptual diffs for a release candidate's runs are complete,
   the results of the candidate are emailed out to the build's owner.

8. The build owner can go into a web UI, inspect the new/old perceptual diffs,
   and mark certain runs as okay even though the perceptual diff showed a
   difference. For example, a new feature will cause a perceptual diff, but
   should not be treated as a failure.

9. The user decides the release candidate looks correct and marks it as good,
   or the user thinks the candidate looks bad and goes back to #2 and begins
   creating a new candidate for that release all over again.


Notes:

- At any time, a user can manually mark any candidate or release as bad. This
  is useful to deal with bugs in the screenshotter, mistakes in approving a
  release candidate, rolling back to an earlier version, etc.

- As soon as a new release name is cut for a build, the last candidate of
  the last release is marked as good if there is no other good candidate. This
  lets the API establish a "baseline" release easily for first-time users.

- Only one release candidate may be receiving runs for a build at a time.

- Failure status can be indicated for a run at the capture phase or the
  diff phase. The API assumes that the same user that indicated the failure
  will also provide a log for the failing process so it can be inspected
  manually for a root cause. Uploading image artifacts for failed runs is
  not supported.
"""

import datetime
import hashlib
import functools
import json
import logging
import mimetypes
import time
from datetime import datetime

# Local libraries
import flask
from flask import Flask, abort, g, request, url_for
from werkzeug.exceptions import HTTPException

# Local modules
from . import app
from . import db
from dpxdt import constants
from dpxdt.client import workers
from dpxdt.client import fetch_worker
from dpxdt.client import pdiff_worker

from dpxdt.server import auth
from dpxdt.server import emails
from dpxdt.server import models
from dpxdt.server import signals
from dpxdt.server import work_queue
from dpxdt.server import utils
from ..tools.site_diff import SiteDiff
from api import _enqueue_capture, _find_last_good_run

import gflags
FLAGS = gflags.FLAGS

# def _create_initial_run
#     return

@app.route('/api/create_build', methods=['POST'])
@utils.retryable_transaction()
def create_build():

    build_name = request.form.get('name', type=str)
    utils.jsonify_assert(build_name, 'supply a build name')

    #check for another build by this name
    build = db.session.query(models.Build).filter(models.Build.name == build_name).first()

    if build:
        return flask.jsonify(
                success=False,
                existing_build_id=build.id,
                message="A build by that name already exists"
                )

    build = models.Build(name=build_name)
    build.public = True

    db.session.add(build)
    db.session.flush()

    auth.save_admin_log(build, created_build=True, message=build.name)

    db.session.commit()

    logging.info('Created build via UI: build_id=%r, name=%r',
                 build.id, build.name)

    return flask.jsonify(
            success=True,
            build_id=build.id,
            )

@app.route('/api/release_and_run', methods=['POST'])
@utils.retryable_transaction()
def release_and_run():

    build = request.form.get('build', type=int)
    url = request.form.get('url', type=str)
    #
    utils.jsonify_assert(build, 'must supply a build')
    utils.jsonify_assert(url, 'must supply a url')
    #
    # datestr = datetime.now().strftime("%Y%m%d-%H%M%S")
    #
    # build = models.Build.query.filter_by(id=build).first()
    #
    # #create a release
    # release_name = request.form.get('release_name', default=datestr)
    #
    # release = models.Release(
    #     name=release_name,
    #     url=url,
    #     number=1,
    #     build_id=build.id)
    #
    # last_candidate = (
    #     models.Release.query
    #     .filter_by(build_id=build.id, name=release_name)
    #     .order_by(models.Release.number.desc())
    #     .first())
    #
    # if last_candidate:
    #     release.number += last_candidate.number
    #
    #     if last_candidate.status == models.Release.PROCESSING:
    #         canceled_task_count = work_queue.cancel(
    #             release_id=last_candidate.id)
    #         logging.info('Canceling %d tasks for previous attempt '
    #                      'build_id=%r, release_name=%r, release_number=%d',
    #                      canceled_task_count, build.id, last_candidate.name,
    #                      last_candidate.number)
    #         last_candidate.status = models.Release.BAD
    #         db.session.add(last_candidate)
    #
    # db.session.add(release)
    # db.session.commit()
    #
    # signals.release_updated_via_api.send(app, build=build, release=release)
    #
    # logging.info('Created release: build_id=%r, release_name=%r, url=%r, '
    #              'release_number=%d', build.id, release.name,
    #              url, release.number)
    #
    #
    # #create a run or runs
    # run = models.Run(
    #         release_id=release.id,
    #         name=datestr,
    #         status=models.Run.DATA_PENDING)
    #
    # db.session.add(run)
    # db.session.flush()
    #
    # # current_url = request.form.get('url', type=str)
    # config_data = request.form.get('config', default='{}', type=str)
    # # utils.jsonify_assert(current_url, 'url to capture required')
    # # utils.jsonify_assert(config_data, 'config document required')
    #
    # config_artifact = _enqueue_capture(build, release, run, url, config_data)
    #
    # _, last_good_run = _find_last_good_run(build)
    # if last_good_run:
    #     run.ref_url = last_good_run.url
    #     run.ref_image = last_good_run.image
    #     run.ref_log = last_good_run.log
    #     run.ref_config = last_good_run.config
    #
    # db.session.add(run)
    # db.session.commit()
    # signals.run_updated_via_api.send(app, build=build, release=release, run=run)

#------

    # ref_url = request.form.get('ref_url', type=str)
    # ref_config_data = request.form.get('ref_config', type=str)
    # utils.jsonify_assert(
    #     bool(ref_url) == bool(ref_config_data),
    #     'ref_url and ref_config must both be specified or not specified')

    # if ref_url and ref_config_data:
    #     ref_config_artifact = _enqueue_capture(
    #         build, current_release, current_run, ref_url, ref_config_data,
    #         baseline=True)
    # else:

    # last_good_release = (
    #     models.Release.query
    #     .filter_by(
    #         build_id=build.id,
    #         status=models.Release.GOOD)
    #     .order_by(models.Release.created.desc())
    #     .first())
    #
    # last_good_run = None
    #
    # if last_good_release:
    #     logging.debug('Found last good release for: build_id=%r, '
    #                   'release_name=%r, release_number=%d',
    #                   build.id, last_good_release.name,
    #                   last_good_release.number)
    #     last_good_run = (
    #         models.Run.query
    #         .filter_by(release_id=last_good_release.id, name=run_name)
    #         .first())
    #     if last_good_run:
    #         logging.debug('Found last good run for: build_id=%r, '
    #                       'release_name=%r, release_number=%d, '
    #                       'run_name=%r',
    #                       build.id, last_good_release.name,
    #                       last_good_release.number, last_good_run.name)
    #
    # return last_good_release, last_good_run
    #
    #
    #
    # _, last_good_run = _find_last_good_run(build)
    # if last_good_run:
    #     current_run.ref_url = last_good_run.url
    #     current_run.ref_image = last_good_run.image
    #     current_run.ref_log = last_good_run.log
    #     current_run.ref_config = last_good_run.config

    # db.session.add(current_run)
    # db.session.commit()

    # signals.run_updated_via_api.send(
    #     app, build=build, release=release, run=current_run)

    # call(["./run_site_diff.sh", "--upload_build_id=%i"%build_id, "--crawl_depth=0", start_url ])

    # config_artifact = _enqueue_capture(
    #     build_id, current_release, current_run, start_url, config_data)

    FLAGS.crawl_depth = 1
    # FLAGS.pdiff_task_max_attempts = 2
    # FLAGS.pdiff_threads = 3

    coordinator = workers.get_coordinator()
    fetch_worker.register(coordinator)
    coordinator.start()
    
    sd = SiteDiff(
        start_url=url,
        ignore_prefixes=None,
        upload_build_id=build,
        upload_release_name=None,
        heartbeat=workers.PrintWorkflow)
    sd.root = True
    #
    coordinator.input_queue.put(sd)
    #
    return flask.jsonify(
            success=True,
            )
###

    # """Requests a new run for a release candidate."""
    # build = g.build
    # current_release, current_run = _get_or_create_run(build)
    #
    # current_url = request.form.get('url', type=str)
    # config_data = request.form.get('config', default='{}', type=str)
    # utils.jsonify_assert(current_url, 'url to capture required')
    # utils.jsonify_assert(config_data, 'config document required')
    #
    # config_artifact = _enqueue_capture(
    #     build, current_release, current_run, current_url, config_data)
    #
    # ref_url = request.form.get('ref_url', type=str)
    # ref_config_data = request.form.get('ref_config', type=str)
    # utils.jsonify_assert(
    #     bool(ref_url) == bool(ref_config_data),
    #     'ref_url and ref_config must both be specified or not specified')
    #
    # if ref_url and ref_config_data:
    #     ref_config_artifact = _enqueue_capture(
    #         build, current_release, current_run, ref_url, ref_config_data,
    #         baseline=True)
    # else:
    #     _, last_good_run = _find_last_good_run(build)
    #     if last_good_run:
    #         current_run.ref_url = last_good_run.url
    #         current_run.ref_image = last_good_run.image
    #         current_run.ref_log = last_good_run.log
    #         current_run.ref_config = last_good_run.config
    #
    # db.session.add(current_run)
    # db.session.commit()
    #
    # signals.run_updated_via_api.send(
    #     app, build=build, release=current_release, run=current_run)
    #
    # return flask.jsonify(
    #     success=True,
    #     build_id=build.id,
    #     release_name=current_release.name,
    #     release_number=current_release.number,
    #     run_name=current_run.name,
    #     url=current_run.url,
    #     config=current_run.config,
    #     ref_url=current_run.ref_url,
    #     ref_config=current_run.ref_config)



# @app.route('/api/flush_workers', methods=['POST'])
# @utils.retryable_transaction()
# def flush_workers():
#     coordinator = workers.get_coordinator()
#     fetch_worker.register(coordinator)
#     coordinator.start()
#     coordinator.input_queue.clear()
