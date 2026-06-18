"""
把每个user所有视频item的文本摘要部分提取出来形成摘要1-X.txt列表
"""


import os
import json

def gene_abstract(user_dir):
    if not os.path.isdir(user_dir):
        raise NotADirectoryError(f"{user_dir} is not a directory.")
    abstracts=[]
    for root,dirs,files in os.walk(user_dir):
        for v_dir in dirs:
            if "_scene_frames" in v_dir:
                continue
            item_json=os.path.join(root,v_dir,"item.json")
            # 创建的新视频摘要目录除外(二次调用等)
            if not os.path.exists(item_json):
                continue
            with open(item_json,"r",encoding='utf-8') as f:
                item=json.loads(f.read())
            assert(isinstance(item,dict))
            abstract=item.get("文本摘要")
            abstracts.append((v_dir,abstract))

        break

    abstract_dir=os.path.join(user_dir,"视频摘要")
    os.makedirs(abstract_dir,exist_ok=True)
    for vname,abstract in abstracts:
        with open(os.path.join(abstract_dir,vname+"_摘要.txt"),"w",encoding="utf-8") as f:
            f.write(abstract)

    print(f"Writing a total of {len(abstracts)} abstracts in {user_dir} successfully!")


if __name__=="__main__":
    upath1="dataset2/本猪小猪"
    upath2="dataset2/水果猎人杨晓洋"
    # gene_abstract(upath1)
    # gene_abstract(upath2)
    a="Hello!Hello"
    print(a.split("?"))



