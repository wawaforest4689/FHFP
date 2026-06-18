## "乡耘奇点"（乡耘智剪）项目演示--运行流程

*启动须知：需要先从https://repo.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe下载Anaconda3安装程序，Anaconda是项目依赖库管理工具。* 



1. ```
   conda create -n fhfp_video_assist python=3.9 -y
   ```

2. ```
   conda activate fhfp_video_assist
   ```

3. ```
   进入你的项目目录并根据依赖文件安装第三方库
   pip install -r requirements.txt
   ```

4. ```
   运行脚本（自动弹出网页）
   python frontend/main_kimi.py
   ```

5. ```
   如果网页没有弹开，在浏览器里面输入http://127.0.0.1:3000即可访问网页端
   ```