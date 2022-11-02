# Apt Mirror

## Description

A small tool that provides ability to mirror any parts (or even all) of Debian and Ubuntu GNU/Linux distributions or any other apt sources which typically are provided by open source developers.

## Usage

### Deployment
The charm can be deployed using `Juju`:
```
juju deploy cs:apt-mirror
```

The charm can handle arbitrary set of upstream DEB sources via setting `mirror-list`. Example below shows a bundle with this charm configured to mirror multiple Ubuntu series (Bionic and Focal) in a single repository and expose this repository via NGINX. Additionally PPAs and external repositories can be mirrored.
```
series: bionic
machines:
  '0':
    series: bionic
services:
  nginx:
    charm: cs:~majduk/nginx
    expose: true
    num_units: 1
    to:
    - '0'
  apt-mirror:
    charm: cs:~majduk/apt-mirror
    expose: true
    num_units: 1
    options:
      mirror-list: |-
        deb http://archive.ubuntu.com/ubuntu bionic main restricted universe multiverse
        deb http://archive.ubuntu.com/ubuntu bionic-updates main restricted universe multiverse
        deb http://archive.ubuntu.com/ubuntu bionic-backports main restricted universe multiverse
        deb http://security.ubuntu.com/ubuntu bionic-security main restricted universe multiverse
        deb http://archive.ubuntu.com/ubuntu focal main restricted universe multiverse
        deb http://archive.ubuntu.com/ubuntu focal-updates main restricted universe multiverse
        deb http://archive.ubuntu.com/ubuntu focal-backports main restricted universe multiverse
        deb http://security.ubuntu.com/ubuntu focal-security main restricted universe multiverse
    to:
    - '0'
relations:
- - nginx
  - apt-mirror
```

The repository needs to be exposed over HTTP using some web server. In the example above, Nginx charmed HTTP server is used to expose the repository.

### Repository consumption

The clients can be pointed to the repository by simply updating the `/etc/apt/sources.list` file to point to the repository, for example `repo.example.com`:
```
deb http://repo.example.com/archive.ubuntu.com/ubuntu focal main restricted universe multiverse
deb http://repo.example.com/archive.ubuntu.com/ubuntu focal-updates main restricted universe multiverse
deb http://repo.example.com/archive.ubuntu.com/ubuntu focal-backports main restricted universe multiverse
deb http://repo.example.com/security.ubuntu.com/ubuntu focal-security main restricted universe multiverse
```

Additional notes:
- The repository can be consumed by units deploed by MAAS. Please refer to [MAAS documentation](https://maas.io/docs/deb/2.9/ui/package-repositories) for the detailed MAAS configuration options.
- The repository can be consumed by Juju deployed models. Please refer to the [discourse](https://discourse.charmhub.io/t/offline-mode-strategies/1071) on offline deployment strategies and [Juju documentation](https://discourse.charmhub.io/t/configuring-models/1151) for more details. In order to use the repository from the Juju model level, simplistic approach is just to use Juju model `apt-mirror` config option and `install-sources` config options for the charms being used.

### Repository management

The repository exposes single, selected snapshot to the clients. After the repository is deployed, it is necessary to pull the upstream packages to the repository:
```
juju run-action apt-mirror/0 synchronize
```
When the action execution completes (depending on the configured mirror list and available bandwidth, it can take a considerable amount of time), snapshot can be created:
```
juju run-action --wait apt-mirror/0 create-snapshot
```
Output of the action contains the created snapshot name, for example `snapshot-20210329092856`.

This snapshot can in turn be published to be used by the clients:
```
juju run-action --wait apt-mirror/0 publish-snapshot name=snapshot-20210329092856
```

Full list of available snapshots can be obtained by running:
```
juju run-action --wait apt-mirror/0 list-snapshots
```
Currently published snapshot is shown in `juju status`.

Repository can be synchronized with the upstream multiple times and multiple snapshots can be created. It's possible to expose any arbitrary snapshot, making it possible to fine tune the packages available to the repository cilents.

The repository allows also specifying a Cron job via `cron-schedule` option, to regularily, automatically sync to the upstream to make sure the repository tracks upstream at a certain delay. To expose the latest packages to the clients, snapshot still needs to be created and published.

## Developing

Create a virtual environment and activate it

    make dev-environment
    source .venv/bin/activate

## Testing

Run lint tests:

    make lint

Run unit tests:

    make unittests
