#!/usr/bin/env python3
"""
_distill_utils.py — 蒸馏工具公共模块

提供批量蒸馏和增量蒸馏共用的工具函数。
"""

from typing import List, Dict, Any


def split_material_into_chunks(content: str, filename: str, max_chars: int = 15000, overlap_paragraphs: int = 2) -> List[Dict[str, Any]]:
    """将素材内容按段落切分成多个chunks

    Args:
        content: 素材内容
        filename: 文件名
        max_chars: 每个chunk的最大字符数（默认15000，约5000中文字）
        overlap_paragraphs: chunk之间重叠的段落数（默认2，避免边界信息丢失）

    Returns:
        切分后的chunk列表，每个chunk包含：
        {
            "index": chunk索引（从1开始）,
            "filename": 原文件名,
            "chunk_suffix": "_chunk{N}" 后缀,
            "result_filename": 结果文件名 "{filename}_chunk{N}.json",
            "content": chunk内容,
            "char_count": 字符数,
            "total_chunks": 总chunk数
        }
    """
    char_count = len(content)

    # 如果文件不超过最大字符数，不需要切分
    if char_count <= max_chars:
        return [{
            "index": 1,
            "filename": filename,
            "chunk_suffix": "",
            "result_filename": f"{filename}.json",
            "content": content,
            "char_count": char_count,
            "total_chunks": 1
        }]

    # 按段落切分（双换行符分割）
    paragraphs = content.split('\n\n')
    chunks = []
    current_chunk = []
    current_length = 0
    chunk_index = 1

    # 用于重叠的段落缓存
    overlap_buffer = []

    for para in paragraphs:
        para_len = len(para) + 2  # 加上 \n\n

        # 如果当前段落加入后会超出限制，且已有内容，则完成当前chunk
        if current_length + para_len > max_chars and current_chunk:
            # 保存当前chunk
            chunk_content = '\n\n'.join(current_chunk)
            chunks.append({
                "index": chunk_index,
                "filename": filename,
                "chunk_suffix": f"_chunk{chunk_index}",
                "result_filename": f"{filename}_chunk{chunk_index}.json",
                "content": chunk_content,
                "char_count": len(chunk_content),
                "total_chunks": 0  # 稍后填充
            })

            chunk_index += 1

            # 保存最后几个段落用于重叠
            overlap_buffer = current_chunk[-overlap_paragraphs:] if len(current_chunk) >= overlap_paragraphs else current_chunk[:]

            # 开始新chunk（从重叠段落开始）
            current_chunk = list(overlap_buffer)
            current_length = sum(len(p) + 2 for p in current_chunk)

        # 添加当前段落
        current_chunk.append(para)
        current_length += para_len

    # 添加最后一个chunk
    if current_chunk:
        chunk_content = '\n\n'.join(current_chunk)
        chunks.append({
            "index": chunk_index,
            "filename": filename,
            "chunk_suffix": f"_chunk{chunk_index}",
            "result_filename": f"{filename}_chunk{chunk_index}.json",
            "content": chunk_content,
            "char_count": len(chunk_content),
            "total_chunks": 0  # 稍后填充
        })

    # 填充总chunk数
    total = len(chunks)
    for chunk in chunks:
        chunk["total_chunks"] = total

    return chunks
