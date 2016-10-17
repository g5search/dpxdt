#!/bin/bash    

#injecting the env vars to connect to the database
#sed -i "s#sqlalchemy.url = driver://user:pass@localhost/dbname#sqlalchemy.url = driver://i$DB_USER:$DB_PASSWORD@$DB_HOST/$DB_NAME#g" alembic.ini

#run things
./run_combined.sh
