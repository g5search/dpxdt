#!/bin/bash    

#injecting the env vars to connect to the database
sed -i "s#sqlalchemy.url = driver://DB_USER:DB_PASSWORD@DB_HOST/DB_NAME#sqlalchemy.url = mysql+mysqldb://$DB_USER:$DB_PASSWORD@$DB_HOST/$DB_NAME#g" alembic.ini

#cd /dpxdt
#underpants &
#cd -

oauth2_proxy --upstream=$OAUTH2_PROXY_UPSTREAM --email-domain="$OAUTH2_PROXY_EMAIL_DOMAIN" --http-address="http://0.0.0.0:80" -skip-auth-regex="/api/((\brelease_and_run\b)|(\bcreate_build\b))" &

#run things
./run_combined.sh
