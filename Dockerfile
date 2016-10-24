FROM golang

MAINTAINER G5 Engineering <engineering@getg5.com>

RUN apt-get update
RUN apt-get install -y build-essential chrpath libssl-dev libxft-dev
RUN apt-get install -y libfreetype6 libfreetype6-dev
RUN apt-get install -y libfontconfig1 libfontconfig1-dev
RUN apt-get install -y git python python-mysqldb imagemagick virtualenv curl wget
RUN apt-get clean all

#WORKDIR /go
#RUN curl -O https://storage.googleapis.com/golang/go1.6.linux-amd64.tar.gz
#RUN tar -xvf go1.6.linux-amd64.tar.gz
##RUN mv go /usr/local
#RUN chmod a+x go
#RUN go get github.com/kellegous/underpants
RUN go get github.com/bitly/oauth2_proxy

#install phantomjs itself
WORKDIR /phantomjs
ENV PHANTOM_JS "phantomjs-2.1.1-linux-x86_64"
RUN wget -U Mozilla https://bitbucket.org/ariya/phantomjs/downloads/$PHANTOM_JS.tar.bz2

RUN tar xvjf $PHANTOM_JS.tar.bz2
RUN mv $PHANTOM_JS /usr/local/share
RUN ln -sf /usr/local/share/$PHANTOM_JS/bin/phantomjs /usr/local/bin

WORKDIR /pip-install
RUN wget https://bootstrap.pypa.io/get-pip.py
RUN python get-pip.py

COPY . /dpxdt/
WORKDIR /dpxdt
RUN virtualenv .
RUN . bin/activate

WORKDIR /
RUN pip install -r dpxdt/requirements.txt
RUN pip install -e /dpxdt 

#RUN echo 'server.db.create_all()' | ./run_shell.sh

EXPOSE 5000

WORKDIR /dpxdt
CMD ./g5-run-dpxdt.sh 
