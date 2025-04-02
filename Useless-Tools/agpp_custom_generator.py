import os
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
exp["unique_sn"] = str(random.randint(0, 999999)).zfill(6)
print("unique sn:", exp["unique_sn"])

for _, __, files in os.walk("."):
    for file in files:
        vid = cv2.VideoCapture(file)
        resolution = int(vid.get(cv2.CAP_PROP_FRAME_HEIGHT))
        episode_stage1 = file.split("[")[1].split("]")[0]
        # 據我所知的
        if "ova" in episode_stage1.lower():
            type = "OVA"
            episode_stage2 = episode_stage1.lower().replace("ova", "")
            if episode_stage2 == "":
                episode = 1
            else:
                episode = int(episode_stage2)
        elif "sp" in episode_stage1.lower():
            type = "SP"
            episode_stage2 = episode_stage1.lower().replace("sp", "")
            if episode_stage2 == "":
                episode = 1
            else:
                episode = int(episode_stage2)
        else:
            type = "normal"
            episode = int(episode_stage1)
        exp["videos"].append({"episode": episode, "resolution": resolution, "type": type, "filename": file})

json.dump(exp, open(".aniGamerPlus.json", "w"))
print("Done.")