# EPGenius Rewriter V2

Docker-based service to run on your VM.   This app downloads an EPGenius M3U playlist and XMLTV EPG, rewrites the IPTV provider information for two providers, and generates separate playlists and EPG files for each provider.  This allows you to not have to publish provider credentials to EPGenius.

The generated EPG files can be used by TiviMate, m3u-editor, or other IPTV applications that support M3U and XMLTV.

## Features

- Downloads the EPGenius M3U playlist
- Downloads the EPGenius XMLTV EPG
- Supports two IPTV providers
- Rewrites Xtream-style `/live/`, `/movie/`, and `/series/` URLs
- Rewrites `#EXT-X-CREDENTIALS` metadata
- Supports provider-specific URLs, usernames, and passwords
- Optional preservation of whitespace in provider paths
- Generates separate playlist and EPG files for each provider
- Serves generated files over HTTP
- Configurable automatic refresh interval
- Provides status information for each provider
- Works with TiviMate
- Works with m3u-editor

---

## Directory

Suggested installation location:

    /srv/docker/epgenius-rewriter-v2/

Project files:

    docker-compose.yml
    Dockerfile
    .env.example
    .gitignore
    requirements.txt
    epgenius_rewriter.py
    README.md
    sample/
    data/
    work/

`data/` and `work/` are runtime directories and should not be committed to Git.

---

## Configuration

Copy the example environment file:

    cp .env.example .env

Edit the file:

    nano .env

### EPGenius Sources

Configure the EPGenius M3U and XMLTV sources:

    EPGENIUS_M3U_URL=...
    EPGENIUS_EPG_URL=...

The M3U source can be an HTTP/HTTPS URL or a Google Drive file URL supported by the application.

### Refresh Interval

The default refresh interval is 60 minutes:

    REFRESH_MINUTES=60

The interval controls how often the container checks the EPGenius sources and regenerates the provider files.

### HTTP Server

The container listens internally on port 8080.

The supplied Docker Compose configuration publishes it on port 38091:

    38091:8080

Set the base URL to the VM address:

    PUBLIC_BASE_URL=http://VM-IP:38091

Replace the IP address with the address of your VM if different.

---

# Provider Configuration

V2 supports two providers.

## Provider 1

Provider 1 is configured using:

    PROVIDER1_NAME=8K
    PROVIDER1_URL=...
    PROVIDER1_USERNAME=...
    PROVIDER1_PASSWORD=...
    PROVIDER1_OUTPUT=provider1
    PROVIDER1_REPLACE_URL=true
    PROVIDER1_PRESERVE_PATH_WHITESPACE=true

The output directory is:

    /data/provider1/

## Provider 2

Provider 2 is configured using:

    PROVIDER2_NAME=DREAM
    PROVIDER2_URL=...
    PROVIDER2_USERNAME=...
    PROVIDER2_PASSWORD=...
    PROVIDER2_OUTPUT=provider2
    PROVIDER2_REPLACE_URL=true
    PROVIDER2_PRESERVE_PATH_WHITESPACE=true

The output directory is:

    /data/provider2/

### Credential Security

Provider usernames and passwords belong in `.env`.

**Never commit `.env` to GitHub.**

The repository's `.gitignore` excludes `.env`.

The `.env.example` file contains configuration placeholders and is safe to commit.

---

# Starting the Container

From the project directory:

    cd /srv/docker/epgenius-rewriter-v2

Build and start:

    docker compose up -d --build

Check the container:

    docker compose ps

View logs:

    docker compose logs -f

---

# Generated Files

The application generates separate output files for each provider.

## Provider 1

    data/provider1/playlist.m3u
    data/provider1/epg.xml.gz
    data/provider1/epg.xml
    data/provider1/status.json

## Provider 2

    data/provider2/playlist.m3u
    data/provider2/epg.xml.gz
    data/provider2/epg.xml
    data/provider2/status.json

---

# HTTP URLs

Assuming the VM address is:

    VM-IP

and the application is published on port `38091`.

## Provider 1  

### Playlist

    http://VM-IP:38091/provider1/playlist.m3u

### Compressed EPG

    http://VM-IP:38091/provider1/epg.xml.gz

### Uncompressed EPG

    http://VM-IP:38091/provider1/epg.xml

### Status

    http://VM-IP:38091/provider1/status.json

---

## Provider 2  

### Playlist

    http://VM-IP:38091/provider2/playlist.m3u

### Compressed EPG

    http://VM-IP:38091/provider2/epg.xml.gz

### Uncompressed EPG

    http://VM-IP:38091/provider2/epg.xml
    
### Status

    http://VM-IP:38091/provider2/status.json

Replace `VM-IP` with the actual VM IP when necessary.

---

# TiviMate

TiviMate should use the HTTP URLs rather than the local filesystem paths.

## Provider 1 

Playlist:

    http://VM-IP:38091/provider1/playlist.m3u

EPG:

    http://VM-IP:38091/provider1/epg.xml.gz

## Provider 2 

Playlist:

    http://VM-IP:38091/provider2/playlist.m3u

EPG:

    http://VM-IP:38091/provider2/epg.xml.gz

The generated M3U also contains the provider-specific EPG URL in the `url-tvg` attribute.

---

# m3u-editor

If the EPGenius V2 data directory is bind-mounted into the m3u-editor container, the generated EPG files can be referenced using local filesystem paths.

Example Docker volume:

    - /srv/docker/epgenius-rewriter-v2/data:/mnt/epgenius-v2:ro

Provider 1 EPG:

    /mnt/epgenius-v2/provider1/epg.xml.gz

Provider 2 EPG:

    /mnt/epgenius-v2/provider2/epg.xml.gz

These paths are intended for applications running inside the Docker environment.

TiviMate cannot use these filesystem paths; TiviMate should use the HTTP URLs described above.

---

# URL Rewriting - if needed (you probably only need epgs for each provider and you can create then map these in epg section of m3u editor

The application processes Xtream-style stream URLs such as:

    /live/username/password/channel.ts

and rewrites the provider URL and credentials according to the configured provider.

It also handles:

    /movie/username/password/...
    /series/username/password/...

Provider-specific URL rewriting can be enabled or disabled with:

    PROVIDER1_REPLACE_URL=true
    PROVIDER2_REPLACE_URL=true

---

# Path Whitespace

Some IPTV providers may use unusual formatting in stream paths.

The following option controls whether path whitespace from the source playlist is preserved:

    PROVIDER1_PRESERVE_PATH_WHITESPACE=true
    PROVIDER2_PRESERVE_PATH_WHITESPACE=true

This is enabled by default.

---

# Refresh Behavior

The application periodically downloads the source M3U and EPG and regenerates both provider outputs.

For example:

    REFRESH_MINUTES=60

means the source files are refreshed approximately once per hour.

A shorter interval can be configured if required.

---

# Status

Each provider has a status file:

    /provider1/status.json
    /provider2/status.json

These contain information about the most recent refresh and rewrite operation.

The logs also report the number of M3U entries processed and stream URLs successfully rewritten.

Example:

    [8K] 4191 entries, 4190/4191 stream URLs rewritten
    [DREAM] 4191 entries, 4190/4191 stream URLs rewritten

A source entry that does not contain a supported Xtream-style URL may remain unchanged.

---

# Updating the Container

To update the application after pulling new code:

    git pull
    docker compose up -d --build

To force a clean rebuild:

    docker compose build --no-cache
    docker compose up -d

---

# Troubleshooting

View the application logs:

    docker compose logs -f epgenius-rewriter-v2

Check container status:

    docker compose ps

Test the Provider 1 playlist:

    curl -I http://127.0.0.1:38091/provider1/playlist.m3u

Test the Provider 2 playlist:

    curl -I http://127.0.0.1:38091/provider2/playlist.m3u

Test the Provider 1 EPG:

    curl -I http://127.0.0.1:38091/provider1/epg.xml.gz

Test the Provider 2 EPG:

    curl -I http://127.0.0.1:38091/provider2/epg.xml.gz

---

# Security

The generated M3U contains IPTV provider credentials in stream URLs.

For this reason:

- Do not commit `.env`
- Do not publish real provider credentials
- Protect the generated playlist URLs
- Avoid exposing port `38091` directly to the public Internet unless appropriate access controls are in place
- Treat generated M3U files as sensitive

The included `.env.example` contains placeholders only.

---

# License

Add an appropriate license here if you intend to distribute the project publicly.
