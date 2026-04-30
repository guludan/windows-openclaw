# 素材采集子session Task Prompt模板

## 模板：人物素材采集

```
采集{人物名}的素材。

人物目录: {person_dir}
中文名: {zh_name}
英文名: {en_name}
领域: {field}

执行以下流程：

### Phase 1: 搜索
用data_search搜索（注意间隔5-10秒）：
```python
import sys; sys.path.insert(0, '/home/tearsyu/.openclaw/workspace/skills/data-search/scripts')
from data_search import search_person
results = search_person("{person_name}", output_dir="{person_dir}/materials")
```

### Phase 2: 补充采集（对搜索结果中没有正文的高质量URL）
对每个高质量URL（学术文章、深度访谈、政策报告），用web_fetch抓取：
1. web_fetch(url) 获取markdown内容
2. 用data_search.save_result()保存：
```python
from data_search import save_result
save_result({"title":"标题","text":"正文","url":"URL","source":"web_fetch"}, "{person_dir}")
```
⚠️ 每次间隔5-10秒

### Phase 3: 论文全文
```python
from data_search import search_academic
papers = search_academic("{en_name}", max_results=15)
# 过滤同名误匹配后逐篇下载
from data_search import download_scihub_pdf
download_scihub_pdf(doi, "{person_dir}/papers/pdfs")
```
⚠️ Sci-Hub间隔15秒

### Phase 4: 著作
搜索著作书名，然后：
```python
from data_search import download_book
download_book(title, "{person_dir}/materials", proxies={'https':'http://172.26.0.1:10808'})
```
⚠️ 下载后必须验证内容是否正确作者的著作

### 保存规则
- ✅ 必须用 save_result() 或直接写 .md 文件
- ❌ 禁止保存 .json 素材文件
- ✅ 文件名自动sanitize（save_result已内置）
- ✅ 保存到 materials/raw/ 目录
- ✅ 论文PDF保存到 papers/pdfs/，提取文本到 papers/raw/
