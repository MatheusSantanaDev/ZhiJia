Import("env")
import os

def before_upload(source, target, env):
    data_dir = os.path.join(env.get("PROJECT_DIR"), "data")
    if os.path.isdir(data_dir):
        env.Execute("pio run --target uploadfs")

env.AddPreAction("upload", before_upload)