Deploy docker with Kathara
===

## Quick start

### Install `kathara`
Follow the [guide](https://github.com/KatharaFramework/Kathara/wiki/Linux#debian-based) here if you yet installed it before.


### Secret setup

Before running the scenario, create a `.env` file with your required secrets:

```bash
echo "GOOGLE_API_KEY=[YOUR KEY HERE]" > .env
```

Inject secrets to configuration file

```bash
. ./inject_secrets.sh
```

### Run

```bash
kathara lstart
```

To terminate the scenario, remember to run

```bash
kathara lclean
```