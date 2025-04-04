import logging
from sqlalchemy import select, create_engine
from sqlalchemy.orm import Session
import os
import sys
import json
import requests
import time
from datetime import datetime, timedelta
from models import auth_app, checkouts
import pandas as pd
import numpy as np
from utils import *
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY")
SQLALCHEMY_DATABASE_URI = os.getenv("SQLALCHEMY_DATABASE_URI")
ssl = os.getenv("ssl")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
MERCHANT_ID = os.getenv("MERCHANT_ID")
FALABELLA_API_KEY = os.getenv("FALABELLA_API_KEY")
FALABELLA_USER = os.getenv("FALABELLA_USER")
PARIS_API_KEY = os.getenv("PARIS_API_KEY")
RIPLEY_API_KEY = os.getenv("RIPLEY_API_KEY")
PREV_DAYS = int(os.getenv("PREV_DAYS"))

st = time.time()
# Setting up logger
logger = logging.getLogger(__name__)
logging.basicConfig(format='%(asctime)s: %(message)s', stream=sys.stdout,
                    level=logging.INFO)

# Making engine
engine = create_engine(SQLALCHEMY_DATABASE_URI,
                       connect_args={
                            "ssl": {
                                "ca":ssl
                                }     
                            }
                       )

datetime.now()

# Get data from tables
logger.info('Retrieving data from db.')
with Session(engine) as session:
    last_auth = session.scalar(select(auth_app).order_by(auth_app.expire.desc()))
    result = session.scalar(select(checkouts).order_by(checkouts.fecha.desc()))
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    last_update = datetime.now() - timedelta(days=PREV_DAYS) # One day before to update changes of recents sells
    last = last_update.strftime("%Y-%m-%dT%H:%M:%S")

#print("Ultima actualizacion ",last)
#print("Ahora ",now)

if last_auth == None:
    logger.error("Failed authentication")
    sys.exit(0)
        
diff = datetime.now() - last_auth.expire
# The token expired
if diff.total_seconds()/3600 > 6:
    logger.warning('Refresh token expired.')
    sys.exit(0)

# Decrypt token
token = decrypt(last_auth.token, SECRET_KEY)

# Get checkouts data
logger.info('Recolectando datos de ventas')
merchant_id = MERCHANT_ID
url = f"https://app.multivende.com/api/m/{merchant_id}/checkouts/light/p/1?_updated_at_from={last}&_updated_at_to={now}"
headers = {
        'Authorization': f'Bearer {token}'
}
# Get id data from the checkouts
response = requests.request("GET", url, headers=headers)
try:
    response = response.json()
except Exception as e:
    logger.error(f'Hubo un error {e}: {response.text}')
    
pages = response["pagination"]["total_pages"]
ids= []
# Extract all ids
logger.info('Cargando ids de ventas.')
for p in range(0, pages):
    url = f"https://app.multivende.com/api/m/{merchant_id}/checkouts/light/p/{p+1}?_updated_at_from={last}&_updated_at_to={now}"
    data = requests.get(url, headers=headers)
    try:
        data = data.json()
    except Exception as e:
        logger.error(f'Hubo un error {e}: {response.text}')
    
    for d in data["entries"]:
        ids.append(d["_id"])

# Now the information completed
logger.info('Cargando informacion de ventas.')
ventas = []
#count = 0
logger.info(f'Total items read: {len(ids)}')
for id in ids:
    tmp = {}
    url = f"https://app.multivende.com/api/checkouts/{id}"
    checkout = requests.get(url, headers=headers)
    try:
        checkout = checkout.json()
        checkout['soldAt']
        #count = count + 1
        #print("Checkout agregado, cuenta: ", count)
        #print(checkout)
        #print("\n\n")
    except Exception as e:
        logger.error(f"Error {e}: {checkout}")
        
    tmp["fecha"] = checkout["soldAt"]
    tmp["nombre"] = checkout["Client"]["fullName"]
    tmp["n venta"] = checkout["CheckoutLink"]["externalOrderNumber"] # Numero de orden en marketplace
    tmp["id"] = checkout["CheckoutLink"]["CheckoutId"] # Codigo en multivende
    tmp["estado entrega"] = checkout["deliveryStatus"]
    tmp["costo de envio"] = checkout["DeliveryOrderInCheckouts"][0]["DeliveryOrder"]["cost"]
    tmp["market"] = checkout["origin"]
    tmp["mail"] = checkout["Client"]["email"]
    tmp["phone"] = checkout["Client"]["phoneNumber"]
    # Try to find the billing files
    try:
        url = f"https://app.multivende.com/api/checkouts/{id}/electronic-billing-documents/p/1"
        billing = requests.get(url, headers=headers).json()
        tmp["estado boleta"] = billing["entries"][-1]["ElectronicBillingDocumentFiles"][-1]["synchronizationStatus"]
        tmp["url boleta"] = billing["entries"][-1]["ElectronicBillingDocumentFiles"][-1]["url"]
    except:
        tmp["estado boleta"] = None
        tmp["url boleta"] = None
        
    # Getting all status of ventas
    tmp["estado venta"] = []
    for status in checkout["CheckoutPayments"]:
        tmp["estado venta"].append(status["paymentStatus"])
    # For each item we split the checkout
    for product in checkout["CheckoutItems"]:
        item = tmp.copy()
        item["codigo producto"] = product["code"]
        item["nombre producto"] = product["ProductVersion"]["Product"]["name"]
        item["id padre producto"] = product["ProductVersion"]["ProductId"]
        item["id hijo producto"] = product["ProductVersionId"]
        item["cantidad"] = product["count"]
        item["precio"] = product["gross"]
        ventas.append(item)

# Load data to be processed
logger.info('Limpiando los datos.')
df = pd.DataFrame(ventas)
df["fecha"] = pd.to_datetime(df["fecha"])
df["fecha"] = df["fecha"].dt.tz_convert(None)
df= df.fillna(np.nan)
for i in df["estado venta"].index:
    df.loc[i, "estado venta"] = df["estado venta"][i][-1]
    
df = df.replace({np.nan: None})

logger.info('Cargando a la DB.')
check_difference_and_update_checkouts(df, checkouts, engine)
et = time.time()
elapsed_time = et - st

hours = elapsed_time // 3600
minutes = (elapsed_time % 3600) // 60
secs = elapsed_time % 60

logger.info(f'Execution time: {hours} hours {minutes} minutes {secs} seconds')