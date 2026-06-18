from pathlib import Path


class FileStorage:
    """
    本地文件存储管理器：所有视频/BGM/输出按项目隔离，真实写入磁盘。
    通过 FastAPI StaticFiles 挂载 /storage 后，前端可直接用 URL 在线播放。
    """

    def __init__(self, base_dir: str = "storage"):
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _project_dir(self, project_id: str) -> Path:
        return self.base_dir / "projects" / project_id

    def save_segment(self, project_id: str, segment_id: str,
                     file_data: bytes, filename: str) -> str:
        """保存片段视频，返回可在线访问的URL路径"""
        seg_dir = self._project_dir(project_id) / "segments" / segment_id
        seg_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(filename).suffix.lower() or ".mp4"
        safe_name = f"video{ext}"
        file_path = seg_dir / safe_name
        with open(file_path, 'wb') as f:
            f.write(file_data)
        return f"/storage/projects/{project_id}/segments/{segment_id}/{safe_name}"

    def save_bgm(self, project_id: str, file_data: bytes, filename: str) -> str:
        """保存背景音乐"""
        bgm_dir = self._project_dir(project_id) / "bgm"
        bgm_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(filename).suffix.lower() or ".mp3"
        safe_name = f"bgm{ext}"
        file_path = bgm_dir / safe_name
        with open(file_path, 'wb') as f:
            f.write(file_data)
        return f"/storage/projects/{project_id}/bgm/{safe_name}"

    def get_output_path(self, project_id: str, filename: str) -> Path:
        """获取输出文件绝对路径（自动创建目录）"""
        out_dir = self._project_dir(project_id) / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir / filename

    def get_absolute_path(self, url_path: str) -> Path:
        """将 /storage/... URL 转换为本地绝对路径"""
        relative = url_path.lstrip('/').replace('storage/', '', 1)
        return self.base_dir / relative
