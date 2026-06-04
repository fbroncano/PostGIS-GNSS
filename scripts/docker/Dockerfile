FROM postgres:18-trixie

LABEL author="fernando"

RUN apt-get update \
      && apt-cache showpkg postgresql-18-postgis-3 \
      && apt-get install -y --no-install-recommends \
           ca-certificates \
           postgresql-18-postgis-3=3.6.1+dfsg-1.pgdg13+1 \
           postgresql-18-postgis-3-scripts \
      && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /docker-entrypoint-initdb.d
COPY ./initdb-postgis.sh /docker-entrypoint-initdb.d/10_postgis.sh
COPY ./update-postgis.sh /usr/local/bin
