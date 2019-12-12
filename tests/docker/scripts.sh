#!/usr/bin/env bash
# A NUMBER OF RANDOM DOCKER SCRIPTS


# login and record commands
docker run --interactive --tty ubuntu bash

# deliver env vars to image
docker run -it --user app --env-file ./tests/docker/public_etl.env --mount source=public_etl_state,destination=/app/logs mo-threads bash

# COPY FILES INTO IMAGE
docker cp . 9fb5624531ae:/app

# SAVE DOCKER PROCESS TO IMAGE
docker commit cf26c8d0d44 mo-threads

# make a dockerfile with those commands

# setup cron
https://jonathas.com/scheduling-tasks-with-cron-on-docker/

# save image for later
https://stackoverflow.com/questions/19585028/i-lose-my-data-when-the-container-exits#19616598


# ADD VOLUME TO STORE STATE

cd ~/Bugzilla-ETL  # ENSURE YOU ARE IN THE ROOT OF THE Bugzilla-ETL REPO
docker build --file resources\docker\dev.dockerfile --no-cache --tag mo-threads .

docker run --interactive --tty --user app --env-file ../../dev_private_etl.env --mount source=public_etl_state,destination=/app/logs mo-threads bash
docker run --user app --env-file ../../dev_private_etl.env --mount source=public_etl_state,destination=/app/logs mo-threads

python bugzilla_etl/bz_etl.py --settings=tests/docker/config.json

# CLEANUP
https://www.calazan.com/docker-cleanup-commands/
