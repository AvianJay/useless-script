import os
import re
import sys
import cv2
import json
import random

exp = {"videos": []}

exp["anime_name"] = os.path.basename(os.getcwd())
print("Anime name:", exp["anime_name"])
if len(sys.argv)>1:
    exp["source"] = sys.argv[1]
else:
    exp["source"] = input("Source?: ")

# try to get unique sn from existing .aniGamerPlus.json
if os.path.exists(".aniGamerPlus.json"):
    try:
        existing_exp = json.load(open(".aniGamerPlus.json", "r"))
        exp["unique_sn"] = existing_exp.get("unique_sn", str(random.randint(0, 999999)).zfill(6))
        print("Found existing unique sn:", exp["unique_sn"])
    except:
        pass
else:
    exp["unique_sn"] = str(random.randint(0, 999999)).zfill(6)
print("unique sn:", exp["unique_sn"])

for _, __, files in os.walk("."):
    for file in files:
        if not file.lower().endswith(('.mp4', '.mkv', '.avi', '.mov', '.wmv')):
            continue
        vid = cv2.VideoCapture(file)
        resolution = int(vid.get(cv2.CAP_PROP_FRAME_HEIGHT))
        episode_stage1 = file.split("[")[1].split("]")[0]
        if episode_stage1.isdigit():
            type = "normal"
            episode = int(episode_stage1)
        elif episode_stage1.isalpha():
            type = episode_stage1
            episode = 1
        else:
            match = re.match(r'^([A-Za-z]+)(\d+)$', episode_stage1)
            if match:
                type = match.group(1)
                episode = match.group(2)
            else:
                print("Failed to parse episode", episode_stage1)
                continue
        exp["videos"].append({"episode": episode, "resolution": resolution, "type": type, "filename": file})

json.dump(exp, open(".aniGamerPlus.json", "w"))
print("Done.")