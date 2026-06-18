import enum
import json
import numpy as np
from sqlalchemy import create_engine, Column, Integer, String, Text, Enum
from sqlalchemy.orm import declarative_base, sessionmaker
from sentence_transformers import SentenceTransformer

# ==========================================
# 1. 数据库配置与模型定义 (纯关系型，无需pgvector)
# ==========================================
DATABASE_URL = "postgresql://postgres:123321@localhost:5432/video_db"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class DatasetType(enum.Enum):
    PUBLIC = "public"  # 优质公共数据
    PERSONAL = "personal"  # 用户自己的作品集


class VideoAsset(Base):
    __tablename__ = 'video_assets'

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_type = Column(Enum(DatasetType), nullable=False, index=True)
    owner_id = Column(String(255), nullable=True, index=True)

    # 组长 TXT 中的传统关系型标签
    video_author = Column(String(255))
    video_name = Column(String(512))
    video_duration = Column(String(50))
    category_l1 = Column(String(100), index=True)
    category_l2 = Column(String(100), index=True)
    shooting_season = Column(String(50))
    shooting_scene = Column(String(100))
    person_count = Column(Integer)
    video_mode = Column(String(100))

    # 数据统计
    likes = Column(Integer, default=0)
    comments = Column(Integer, default=0)
    favorites = Column(Integer, default=0)
    shares = Column(Integer, default=0)

    # 核心内容字段
    highlight_text = Column(Text)
    summary = Column(Text)
    clip_description = Column(Text)
    full_video_url = Column(String(1024))

    # 🌟 关键改动：这里不再用 VECTOR 类型，而是把向量转成 JSON 字符串存在 TEXT 字段里
    summary_vector_json = Column(Text)


# ==========================================
# 2. 本地 Embedding 模型与数学计算
# ==========================================
print("正在加载本地嵌入 model (请保持网络畅通)...")
model = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')


def get_embedding(text: str):
    """生成文本的嵌入向量"""
    if not text:
        return [0.0] * 384
    return model.encode(text).tolist()


def cosine_similarity(v1, v2):
    """用纯 Python (Numpy) 计算两个向量的余弦相似度"""
    dot_product = np.dot(v1, v2)
    norm_v1 = np.linalg.norm(v1)
    norm_v2 = np.linalg.norm(v2)
    if norm_v1 == 0 or norm_v2 == 0:
        return 0.0
    return float(dot_product / (norm_v1 * norm_v2))


# ==========================================
# 3. 文本解析核心工具
# ==========================================
def parse_txt_to_dict(txt_content: str) -> dict:
    data = {}
    lines = txt_content.strip().split('\n')
    mapping = {
        "视频作者": "video_author", "视频名称": "video_name", "视频时长": "video_duration",
        "一级门类": "category_l1", "二级门类": "category_l2", "拍摄时间": "shooting_season",
        "拍摄场景": "shooting_scene", "人物数量": "person_count", "视频模式": "video_mode",
        "喜欢": "likes", "评论": "comments", "收藏": "favorites", "转发": "shares"
    }
    for line in lines:
        if not line.strip() or ":" not in line: continue
        parts = line.split(':', 1)
        key, value = parts[0].strip(), parts[1].strip()
        if key in mapping:
            db_key = mapping[key]
            if db_key == "person_count":
                data[db_key] = int(value) if value.isdigit() else 0
            elif db_key in ["likes", "comments", "favorites", "shares"]:
                if '万' in value:
                    data[db_key] = int(float(value.replace('万', '')) * 10000)
                else:
                    data[db_key] = int(value) if value.isdigit() else 0
            else:
                data[db_key] = value
    return data


# ==========================================
# 4. 业务逻辑：纯 Python 实现的混合检索
# ==========================================
def python_hybrid_search(db_session, query_text: str, dataset_type: DatasetType, user_id: str = None,
                         category_l2: str = None, limit: int = 3):
    # 1. 把搜索词转成向量
    query_vector = get_embedding(query_text)

    # 2. 先利用 PostgreSQL 的关系型特长，把符合条件的数据全捞出来（初筛）
    query = db_session.query(VideoAsset).filter(VideoAsset.dataset_type == dataset_type)
    if dataset_type == DatasetType.PERSONAL:
        query = query.filter(VideoAsset.owner_id == user_id)
    if category_l2:
        query = query.filter(VideoAsset.category_l2 == category_l2)

    candidates = query.all()

    # 3. 在 Python 内存中计算余弦相似度并排序
    scored_candidates = []
    for item in candidates:
        if item.summary_vector_json:
            # 将数据库存的 JSON 字符串还原为 Python 列表（向量）
            item_vector = json.loads(item.summary_vector_json)
            score = cosine_similarity(query_vector, item_vector)
            scored_candidates.append((score, item))

    # 按分数从高到低排序，并截取前 limit 个结果
    scored_candidates.sort(key=lambda x: x[0], reverse=True)
    return [item for score, item in scored_candidates[:limit]]


# ==========================================
# 5. 主程序运行流程
# ==========================================
if __name__ == "__main__":
    print("正在连接数据库并创建表结构...")
    Base.metadata.create_all(engine)
    db = SessionLocal()

    try:
        sample_txt = """
        视频作者: 一个卖土豆的胖子
        视频名称: 东北土豆哥教你挑黄瓜，油麦菜？什么样的油麦菜，黄瓜好吃
        视频时长: 1分22秒
        一级门类: 种植业产品
        二级门类: 蔬菜
        拍摄时间: 夏季
        拍摄场景: 集市
        人物数量: 1
        视频模式: 产品介绍
        数据统计
        喜欢: 4.3万
        评论: 131
        收藏: 1
        转发: 926
        """

        print("\n--- 步骤 1: 解析组长的 TXT 文本 ---")
        parsed_meta = parse_txt_to_dict(sample_txt)

        mock_summary = "介绍在东北集市上如何挑选新鲜黄瓜和油麦菜的实用技巧。"
        mock_highlight = "总结了看颜色和捏硬度两个核心亮点。"
        mock_clip_desc = "视频前30秒展示了黄瓜的特写。"

        print("\n--- 步骤 2: 生成摘要的向量嵌入 ---")
        vector_data = get_embedding(mock_summary)
        # 🌟 关键改动：把向量序列化成字符串存入
        vector_json_str = json.dumps(vector_data)

        print("\n--- 步骤 3: 数据写入纯关系型数据库 ---")
        db.query(VideoAsset).delete()  # 清空旧数据

        public_video = VideoAsset(
            dataset_type=DatasetType.PUBLIC,
            highlight_text=mock_highlight,
            summary=mock_summary,
            clip_description=mock_clip_desc,
            full_video_url="http://storage.local/public/videos/v123.mp4",
            summary_vector_json=vector_json_str,  # 存入字符串
            **parsed_meta
        )
        db.add(public_video)
        db.commit()
        print("数据成功写入！不需要任何数据库插件！")

        print("\n--- 步骤 4: 测试无插件混合检索 ---")
        test_query = "新鲜食材怎么选"
        print(f"用户输入: '{test_query}' | 条件: 公共库 + 蔬菜分类")

        results = python_hybrid_search(db, test_query, DatasetType.PUBLIC, category_l2="蔬菜")

        print(f"检索到符合条件的视频数量: {len(results)}")
        for item in results:
            print(f"-> 匹配视频: [{item.video_name}] | 作者: {item.video_author} | 点赞: {item.likes}")

    finally:
        db.close()