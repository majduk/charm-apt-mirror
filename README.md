# Apt Mirror

## Description

A small tool that provides ability to mirror any parts (or even all) of Debian and Ubuntu GNU/Linux distributions or any other apt sources which typically provided by open source developers.

## Usage

The charm can be deployed using juju:
```
juju deploy cs:apt-mirror
```

## Developing

Create and activate a virtualenv,
and install the development requirements,

    virtualenv -p python3 venv
    source venv/bin/activate
    pip install -r requirements-dev.txt

## Testing

Just run `run_tests`:

    ./run_tests
