# Systrade Cloud Fetcher

## Setup

1. Copy .env.example to .env and fill with real credentials from md file

2. Install package
```bash
# Python 3.12+
python -m venv venv 
source venv/bin/activate

pip install -r requirements.txt
```

## Usage
```bash
python main.py --source gcp

python main.py --source aws

python main.py --source azure

python main.py --source all
```

View the output in `output/` folder
