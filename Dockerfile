FROM debian:jessie

MAINTAINER G5 Engineering <engineering@getg5.com>

RUN apt-get update
RUN apt-get install -y build-essential chrpath libssl-dev libxft-dev
RUN apt-get install -y libfreetype6 libfreetype6-dev
RUN apt-get install -y libfontconfig1 libfontconfig1-dev
RUN apt-get install -y git python python-mysqldb imagemagick virtualenv curl wget
RUN apt-get clean all

#install phantomjs itself
WORKDIR /phantomjs
ENV PHANTOM_JS "phantomjs-1.9.8-linux-x86_64"
RUN wget https://bitbucket.org/ariya/phantomjs/downloads/$PHANTOM_JS.tar.bz2

RUN tar xvjf $PHANTOM_JS.tar.bz2
RUN mv $PHANTOM_JS /usr/local/share
RUN ln -sf /usr/local/share/$PHANTOM_JS/bin/phantomjs /usr/local/bin

WORKDIR /pip-install
RUN wget https://bootstrap.pypa.io/get-pip.py
RUN python get-pip.py

COPY ./* /dpxdt/
WORKDIR /dpxdt
RUN virtualenv .
RUN . bin/activate

RUN pip install -r requirements.txt
RUN pip install -e .

#RUN echo 'server.db.create_all()' | ./run_shell.sh

EXPOSE 5000

CMD ./g5-run-dpxdt.sh > /var/log/dpxdt.log
