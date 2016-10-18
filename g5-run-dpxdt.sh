#!/bin/bash    

#injecting the env vars to connect to the database
sed -i "s#sqlalchemy.url = driver://DB_USER:DB_PASSWORD@DB_HOST/DB_NAME#sqlalchemy.url = mysql+mysqldb://$DB_USER:$DB_PASSWORD@$DB_HOST/$DB_NAME#g" alembic.ini

#run things
./run_combined.sh
