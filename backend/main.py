from flask import Flask, redirect, request, url_for
from flask_cors import CORS, cross_origin
import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction, OpenCLIPEmbeddingFunction
from chromadb.utils.data_loaders import ImageLoader
from groq import Groq

from google_auth_oauthlib.flow import InstalledAppFlow

import json
import re
import configparser
import requests
from pathlib import Path

from gmail import GmailAPI


CONFIG = configparser.ConfigParser()
CONFIG.read("TOKENS.ini")

GROQ_API_KEY = CONFIG["SECRETS"]["GROQ_API_KEY"]

# flask --app backend/main --debug run

app = Flask(__name__)
CORS(app, origins="*")


groq_client = Groq(
    api_key=GROQ_API_KEY,
)

gmail = GmailAPI()



def download_images(urls: list[str]) -> list[str]:
    result = []
    for url in urls:
        try:
            img_data = requests.get(url).content
            local_path = Path("./backend/data/images/") / Path(url).parts[-1]
            with open(local_path, "wb") as fobj:
                fobj.write(img_data)
            result.append(str(local_path))
        except:
            pass
    return result


embedder = SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
multimodal_embedder = OpenCLIPEmbeddingFunction()
data_loader = ImageLoader()

chromadb_client = chromadb.Client()
email_collection = chromadb_client.create_collection(
    "email_collection",
    metadata={"hnsw:space": "cosine"},
    embedding_function=multimodal_embedder
)


# collection = chromadb_client.create_collection(
#     "emails",
#     metadata={"hnsw:space": "cosine"},
#     embedding_function=embedder,
# )
# multimodal_collection = chromadb_client.create_collection(
#     "emails_and_attachments",
#     metadata={"hnsw:space": "cosine"},
#     embedding_function=multimodal_embedder,
#     data_loader=data_loader
# )

# multimodal_collection.add(
#     ids=["cat", "dog"],
#     uris=download_images(
#         ["https://www.cdc.gov/healthy-pets/media/images/2024/04/Cat-on-couch.jpg",
#          "https://hips.hearstapps.com/hmg-prod/images/dog-puppy-on-garden-royalty-free-image-1586966191.jpg"]
#         )
# )

# img_result = multimodal_collection.query(
#     query_uris=download_images(["https://d6vhjw8wa28ve.cloudfront.net/wp-content/uploads/2021/03/kitten-sitting-on-floor-031621.jpg"]) # A list of strings representing URIs to data
# )

# print(img_result)
# txt_result = multimodal_collection.query(
#     query_texts=["Kitten"]
# )
# print(txt_result)
# txt_result = multimodal_collection.query(
#     query_texts=["Puppy"]
# )
# print(txt_result)


def synth_initialize():
    with open("./backend/data/synth_data.json") as fobj:
        data = json.load(fobj)
        for id, d in enumerate(data):
            merged_data = d["subject"]+" "+re.sub(r"[\n]", " ", d["body"])
            email_collection.add(
                documents=[merged_data], # we embed for you, or bring your own
                metadatas=[{
                    "from": d["from"],
                    "to": d["to"],
                    "subject": d["subject"],
                    "body": d["body"],
                }], # filter on arbitrary metadata!
                ids=[str(id)], # must be unique for each doc
            )


def initialize_live_data():
    print("STARTS")
    emails, image_urls = gmail.get_emails(count=50)
    print("ENDS")
    for id, (email, image_urls) in enumerate(zip(emails, image_urls)):
        if email.body is not None:
            merged_data = email.subject+" "+re.sub(r"[\n]", " ", email.body)
            email_collection.add(
                documents=[merged_data],
                metadatas=[{
                    "from": email.from_,
                    "to": email.to,
                    "subject": email.subject,
                    "body": email.body,
                }],
                ids=[str(id)]
            )

        for image_url in image_urls:
            uris = download_images([image_url])
            if len(uris) > 0:
                email_collection.add(
                    uris=uris,
                    metadatas=[{
                        "email": str(id),
                    }],
                    ids=[str(id)+"I"+str(iid) for iid in range(len(uris))]
                )


@app.get('/result')
def return_result():
    query = request.args.get("query")

    results = email_collection.query(
        query_texts=[query],
        n_results=5,
    )
    
    return results

@app.post('/message')
def ai_message():
    body_json = request.get_json()
    ids = body_json.get("ids")
    prompt = body_json.get("prompt")

    results = email_collection.get(
        ids=ids.split(","),
        # where={"style": "style1"}
    )

    textual_data = ""
    for ind, metadata in enumerate(results["metadatas"]):
        textual_data += f"[#{ind}] An email sent from {metadata['from']} to {metadata['to']} with subject line {metadata['subject']} with content of <<<{metadata['body']}>>>\n"

    # print(textual_data)

    chat_completion = groq_client.chat.completions.create(
        messages=[
            {
                "role": "system",
                "content": f"Helpful assistant that assists users to find their content from emails for the given data, where individual emails are separated by [#]. Your job is to response as concisely as possible with the given context. Please limit your response to 1-2 sentences max. If there is not enough context to answer the question, please let the user know. The user may give you a vague prompt, or ask a direct question. If given a vague prompt, please supply a concise response regarding relevant information to the prompt. Again, only 1-2 sentences max. \n\n {textual_data}"
            },
            {
                "role": "user",
                "content": prompt,
            }
        ],
        model="llama3-8b-8192",
    )

    response = chat_completion.choices[0].message.content

    return {"response": response}


auth_state = None
cred = None

@app.route("/login")
def login():
    auth_url = gmail.login()

    if auth_url:
        return redirect(auth_url)

@app.route("/callback")
def callback():
    if request.args.get('state') != gmail.auth_state:
        raise Exception('Invalid state')
    
    gmail.login_callback(request.url)

    initialize_live_data()

    return redirect(url_for("index"))

# @app.post("/login")
# def login():
#     body_json = request.get_json()
#     creds = body_json.get("credential")
#     client_id = body_json.get("clientId")


# synth_initialize()
# initialize_live_data()
