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

import datetime, hashlib, functools, json, logging, mimetypes, time, os
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

def pull_inject_code():

    try:
        if os.environ['GITHUB_TOKEN'] and os.environ['INJECT_DIR']:

            if os.path.exists(os.environ['INJECT_DIR']):
                cmd = "cd %s ; git pull https://%s@github.com/g5search/dpxdt-inject" % (os.environ['INJECT_DIR'], os.environ['GITHUB_TOKEN'])
            else:
                cmd = "git clone https://%s@github.com/g5search/dpxdt-inject %s" % \
                (os.environ['GITHUB_TOKEN'], os.environ['INJECT_DIR'])

            print cmd
            os.system(cmd)
    except:
        return 0

    return 1


def _create_build(build_name):

    build = models.Build(name=build_name)
    build.public = True

    db.session.add(build)
    db.session.flush()

    auth.save_admin_log(build, created_build=True, message=build.name)

    db.session.commit()

    logging.info('Created build via UI: build_id=%r, name=%r',
                 build.id, build.name)

    return db.session.query(models.Build).filter(models.Build.name == build_name).first()

@app.route('/api/create_build', methods=['POST'])
@utils.retryable_transaction()
def create_build():

    passed_key = request.form.get('G5_DPXDT_API_KEY', default=None, type=str)
    if passed_key != os.environ['G5_DPXDT_API_KEY']:
        return flask.jsonify(error="invalid or missing API key")

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

    _create_build(build_name)

    return flask.jsonify(
            success=True,
            build_id=build.id,
            )

@app.route('/api/release_and_run', methods=['POST'])
@utils.retryable_transaction()
def release_and_run():

    passed_key = request.form.get('G5_DPXDT_API_KEY', default=None, type=str)
    if passed_key != os.environ['G5_DPXDT_API_KEY']:
        return flask.jsonify(error="invalid or missing API key")

    build = request.form.get('build', default=None, type=int)
    url = request.form.get('url', default=None, type=str)
    name = request.form.get('name', default=None, type=str)
    depth = request.form.get('depth', default=1, type=int)

    #name supercedes build
    if name:

        msg = "Build determined via name: %s. " % name
        bd = models.Build.query.filter_by(name=name).first()

        if not bd:
            bd = _create_build(name)
            msg += " Build did not exist, created it. "
            # return flask.jsonify(error="build by that name does not exist.")

        build = bd.id

    else:
        msg = "Build id taken from passed arg: %s. " % build

    #however we determined the build, make sure we have a url
    if not url:

        rel = models.Release.query.filter_by(build_id=build)\
        .order_by(models.Release.created.desc()).first()

        if not rel:
            return flask.jsonify(error="no url provided and no previous releases to extrapolate from.")

        url = rel.url
        msg += "url determined via last release in the build: %s. " % url

    else:
        msg += "url taken from passed arg: %s. " % url

    utils.jsonify_assert(build, 'must supply a build or a name')
    utils.jsonify_assert(url, 'must supply a url or a name')

    FLAGS.crawl_depth = depth

    pull_inject_code()

    coordinator = workers.get_coordinator()
    fetch_worker.register(coordinator)
    coordinator.start()

    sd = SiteDiff(
        start_url=url,
        ignore_prefixes=None,
        upload_build_id=build,
        upload_release_name=url,
        heartbeat=workers.PrintWorkflow)
    sd.root = True

    coordinator.input_queue.put(sd)

    msg += "Job(s) started."

    return flask.jsonify(
            success=True,
            msg=msg,
            )

### vvv Code that doesn't use the task, but doesn't crawl ###

# build = request.form.get('build', type=int)
# url = request.form.get('url', type=str)
#
# utils.jsonify_assert(build, 'must supply a build')
# utils.jsonify_assert(url, 'must supply a url')

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

# @app.route('/api/flush_workers', methods=['POST'])
# @utils.retryable_transaction()
# def flush_workers():
#     coordinator = workers.get_coordinator()
#     fetch_worker.register(coordinator)
#     coordinator.start()
#     coordinator.input_queue.clear()
