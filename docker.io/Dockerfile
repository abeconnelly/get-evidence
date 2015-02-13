FROM ubuntu:14.10

RUN  apt-get update && apt-get install -y apache2 apache2-threaded-dev ca-certificates cron curl git-core \
  libapache2-mod-php5 libapache2-mod-python libmysqlclient15-dev \
  make mysql-client mysql-server patch \
  php5 php5-curl php5-dev php5-gd php5-mysql php-db \
  python-biopython python-dev python-mysqldb python-pyrex ruby ruby-dev ruby-curb ruby-safe-yaml ruby-yaml-db jq \
  python-setuptools \
  rsync unzip wget zip at --fix-missing && \
  a2enmod  php5 && \
  a2enmod rewrite && \
  a2enmod expires && \
  a2enmod negotiation

RUN perl -ni~ -e 'print unless /www-data/' /etc/at.deny && \
  chmod a+r /etc/at.deny

## TODO: abeconnelly holds some bug fixes.  Change to official repo
#  when they have been merged
#
RUN cd / && git clone https://github.com/abeconnelly/get-evidence && \
  cd get-evidence && \
  make install && \
  cd /get-evidence/server && \
  chmod u+x genome_analyzer.py
#
# gem install curb yaml && \
RUN cd /root && git clone https://github.com/curoverse/arvados && \
  cd /root/arvados/sdk/python && python setup.py install && \
  cp /root/arvados/sdk/cli/bin/* /usr/bin/ && \
  gem install --no-user-install arvados google-api-client json trollop activesupport andand oj


RUN perl -pi~ -e 's:\n:\ninit_connect=‘SET collation_connection = utf8_unicode_ci’\ninit_connect=‘SET NAMES utf8’\ncharacter-set-server=utf8\ncollation-server=utf8_unicode_ci\nskip-character-set-client-handshake\n: if m:\[mysqld\]:;' /etc/mysql/my.cnf

RUN service mysql start && echo "create database evidence character set = utf8; \
  create user 'evidence'@'localhost' identified by 'shakespeare'; \
  grant all privileges on *.* to 'evidence'@'localhost'; flush privileges ; " | \
  mysql -u root

RUN mkdir -p /home/trait/data && chown www-data:www-data /home/trait

# RUN perl -pi~ -e 's;DocumentRoot /var/www/html;DocumentRoot /get-evidence/public_html\n<Directory /get-evidence/public_html>\nAllowOverride All\nphp_admin_value open_basedir "/get-evidence:/home/trait/data:/usr/share/php:/tmp:/dev/urandom"\nphp_value include_path ".:/get-evidence/public_html:/usr/share/php"\n</Directory>; if m:DocumentRoot /var/www/html:;' /etc/apache2/sites-available/000-default.conf


COPY ./config.php /get-evidence/public_html/config.php
COPY ./apache2.conf /etc/apache2/apache2.conf
COPY ./000-default.conf /etc/apache2/sites-available/000-default.conf
COPY ./php5.conf /etc/apache2/mods-enabled/php5.conf

COPY ./Consumer.php /get-evidence/php-openid/Auth/OpenID/Consumer.php
COPY ./Manager.php /get-evidence/php-openid/Auth/Yadis/Manager.php

RUN service apache2 start && service mysql start && cd /tmp && wget localhost/install.php && \
  wget http://evidence.personalgenomes.org/get-evidence.sql.gz && \
  zcat get-evidence.sql.gz | mysql -u root evidence

# dbSNP and GeneTests
# THIS IS BRITTLE.  The ftp link will surely break in the near future
#
RUN service mysql start && cd /get-evidence && \
  service mysql start && cd /get-evidence && \
  mkdir tmp && \
  wget -O/home/trait/data/genetests-data.txt ftp://ftp.ncbi.nih.gov/pub/GeneTests/data_to_build_custom_reports.txt && \
  chown www-data /home/trait/data/genetests-data.txt && \
  ./import_genetests_data.php /home/trait/data/genetests-data.txt && \
  wget -Otmp/dbsnp.bcp.gz ftp://ftp.ncbi.nih.gov/snp/organisms/human_9606/database/organism_data/b142_SNPChrPosOnRef_106.bcp.gz && \
  ./import_dbsnp.php tmp/dbsnp.bcp.gz

# From http://www.omim.org/downloads
# "
# Johns Hopkins University holds the copyright to OMIM including
# the collective data therein and provides access to these FTP
# files to nonprofit entities. Requests for information regarding
# a license for commercial use of the OMIM database may be sent
# via e-mail to JHTT-Communications@jhmi.edu.
# "
#RUN service mysql start && cd /get-evidence && \
#  wget -Otmp/OmimVarLocusIdSNP.bcp.gz ftp://ftp.ncbi.nlm.nih.gov/snp/organisms/human_9606/database/organism_data/OmimVarLocusIdSNP.bcp.gz && \
#  gunzip tmp/OmimVarLocusIdSNP.bcp.gz && \
#  make import_omim

COPY get-evidence.ini /etc/php5/conf.d/get-evidence.ini

RUN cd /get-evidence/server/script/ && \
  USER=www-data SOURCE=/get-evidence CORE=/get-evidence/server \
  CONFIG=/home/trait/config TMP=/home/trait/tmp \
  DATA=/home/trait/data UPLOAD=/home/trait/upload LOG=/home/trait/log \
  BASE_URL=http://localhost/ ./configure.sh && \
  . /get-evidence/server/script/config-local.sh && \
  sudo -u $USER mkdir -p $TMP $UPLOAD $LOG $CONFIG $DATA

RUN alias source='.' && cd /get-evidence/server/script && \
  export USER=www-data && \
  export SOURCE=/get-evidence CORE=/get-evidence/server && \
  export SCRIPT_DIR=/get-evidence/server/script && \
  . $SCRIPT_DIR/configure.sh && \
  . ./defaults.sh && \
  perl -p -e 's/%([A-Z]+)%/$ENV{$1}/g' \
    < $SOURCE/server/script/genome-analyzer.in \
    > /etc/init.d/genome-analyzer.tmp && \
  chmod 755 /etc/init.d/genome-analyzer.tmp && \
  chown 0:0 /etc/init.d/genome-analyzer.tmp && \
  mv /etc/init.d/genome-analyzer.tmp /etc/init.d/genome-analyzer && \
  update-rc.d genome-analyzer start 20 2 3 4 5 . stop 80 0 1 6 .

#RUN cd /get-evidence/server/script && \
#  . ./config-local.sh && \
#  sudo -u $USER ./install-user.sh

#RUN cd /get-evidence/server && \
#  python setup.py build_ext --inplace && \
#  echo "12 3 * * * $USER cd /get-evidence && make daily" | sudo tee /etc/cron.d/get-evidence


EXPOSE 80

COPY ./startup_and_persist.sh /root/startup_and_persist.sh

COPY ./.arvenv /get-evidence/public_html/.arvenv
COPY ./get-evidence.pipeline /get-evidence/public_html/lib/get-evidence.pipeline
COPY ./submit_GE_pipeline /get-evidence/public_html/submit_GE_pipeline


RUN chown www-data:www-data /get-evidence/public_html/submit_GE_pipeline && \
  chown www-data:www-data /get-evidence/public_html/.arvenv && \
  usermod -G daemon www-data && \
  chmod u+x /get-evidence/public_html/submit_GE_pipeline

#RUN gem install --no-user-install arvados google-api-client json trollop activesupport andand oj

CMD [ "/root/startup_and_persist.sh" ]
