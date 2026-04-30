# 素材质量评分

人物: {person_name} {alias_note}
别名: {all_names}

## 评分标准
- 5: 高质量学术论文/核心著作，原创观点→keep
- 4: 有实质内容的文章/访谈→keep  
- 3: 一般介绍/概述→keep
- 2: 重复/转载/摘要页→discard
- 1: 无关/广告/导航页→discard

只根据预览判断。书籍只提供开头预览，确认作者即可高分。

## 输出格式
严格输出JSON数组，无其他文字：
[{"filename":"xxx","score":4,"reason":"简短理由","action":"keep"}]
score≤2→discard, ≥3→keep

## 素材列表

{materials_list}
