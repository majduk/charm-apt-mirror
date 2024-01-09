# Apt Mirror

> [!NOTE]
> This charm is under maintenance mode. Only critical bug will be handled.

## Description

A small tool that provides ability to mirror any parts (or even all) of Debian and Ubuntu GNU/Linux distributions or any other apt sources which typically are provided by open source developers.

## Usage

### Deployment
The charm can be deployed using `Juju`:
```
juju deploy apt-mirror
```

The charm can handle arbitrary set of upstream DEB sources via setting `mirror-list`. Example below shows a bundle with this charm configured to mirror multiple Ubuntu series (Focal and Jammy) in a single repository and expose this repository via NGINX. Additionally PPAs and external repositories can be mirrored.
```
series: jammy
machines:
  '0':
    series: jammy
services:
  nginx:
    charm: nginx
    expose: true
    num_units: 1
    to:
    - '0'
  apt-mirror:
    charm: apt-mirror
    expose: true
    num_units: 1
    options:
      mirror-list: |-
        deb http://archive.ubuntu.com/ubuntu focal main restricted universe multiverse
        deb http://archive.ubuntu.com/ubuntu focal-updates main restricted universe multiverse
        deb http://archive.ubuntu.com/ubuntu focal-backports main restricted universe multiverse
        deb http://security.ubuntu.com/ubuntu focal-security main restricted universe multiverse
        deb http://archive.ubuntu.com/ubuntu jammy main restricted universe multiverse
        deb http://archive.ubuntu.com/ubuntu jammy-updates main restricted universe multiverse
        deb http://archive.ubuntu.com/ubuntu jammy-backports main restricted universe multiverse
        deb http://security.ubuntu.com/ubuntu jammy-security main restricted universe multiverse
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

Sometimes the packages will become outdated or no longer needed by any snapshot, one can check if any packages can be safely removed by running:
```
juju run-action --wait apt-mirror/0 check-packages
```

If there are some outdated packages, we can clean them up by running:
```
juju run-action --wait apt-mirror/0 clean-up-packages confirm=true
```

Repository can be synchronized with the upstream multiple times and multiple snapshots can be created. It's possible to expose any arbitrary snapshot, making it possible to fine tune the packages available to the repository cilents.

Unnecessary packages are automatically clean up during the synchronization. However, when snapshots are deleted, it is possible that some packages will no longer be needed. In this case, one can check if there are any unneeded packages that can be remove using `check-packages` action. After reviewing the outputs from the `check-packages` action. One can proceed to remove those packages with `clean-up-packages` actions.

The repository allows also specifying a Cron job via `cron-schedule` option, to regularily, automatically sync to the upstream to make sure the repository tracks upstream at a certain delay. To expose the latest packages to the clients, snapshot still needs to be created and published.

The action `synchronize` supports also parameter source, which run synchronize for a specific
mirror. This parameter is used as a regular expression to filter the list of mirrors provided
by the "mirror-list" configuration option. Leave unset to synchronize all mirrors.

The following example synchronizes all mirrors starting with "deb http://ppa.launchpad.net/".
```bash
$ juju run-action --wait apt-mirror/0 -- synchronize source="^deb http://ppa.launchpad.net/.*"
unit-apt-mirror-0:
  UnitId: apt-mirror/0
  id: "90"
  results:
    message: Freed up 0.0 bytes by cleaning 0 packages
    time: "4.845958232879639"
  status: completed
  timing:
    completed: 2023-07-10 10:07:13 +0000 UTC
    enqueued: 2023-07-10 10:07:06 +0000 UTC
    started: 2023-07-10 10:07:08 +0000 UTC
```

## Developing

Create a virtual environment and activate it

    make dev-environment
    source .venv/bin/activate

## Testing

Run complete tests

    make tests

Run lint tests:

    make lint

Run unit tests:

    make unittests

Run functional tests:

    make functional
