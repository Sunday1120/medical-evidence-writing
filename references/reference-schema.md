# 结构化来源与辅助脚本

## 来源与表述分开记录

来源 ID 在草稿、证据表与最终列表之间保持稳定。支持程度属于“表述—来源”的关系，不给整篇论文设置一个通用于所有结论的支持等级。

下面是结构示意，所有名称和结论均为合成占位，不能作为医学证据：

```json
{
  "references": [
    {
      "id": "src-demo",
      "title": "合成示例来源，不是真实论文",
      "type": "paper",
      "used_for": ["C1"],
      "citation_number": 1,
      "verification_status": "not-verified"
    }
  ],
  "claims": [
    {
      "id": "C1",
      "text": "合成示例表述，不可用于临床或发布",
      "sources": [
        {
          "source_id": "src-demo",
          "support_strength": "partial",
          "locator": "示例结果段",
          "limitations": "示例人群有限，不能外推",
          "verification_status": "not-verified"
        }
      ]
    }
  ]
}
```

来源必填：`id`、`title`、`type`、`used_for`。`type` 为 `paper`、`guideline`、`regulatory`、`local-material`、`dataset` 或 `web`。

- `citation_number`：仅正文引用的来源填写，按首次出现顺序从 1 连续编号。仅证据表使用的来源省略该字段，不能用 0、null 或虚构编号补齐。
- `used_for`：支持或参与核对的表述 ID 列表。若交付 `claims`，人工核对其 ID 和来源对应关系是否完整。
- `authors`、`year`、`journal`、`doi`、`pmid`、`pmcid`、`url`：按实际取得的元数据填写，不猜测。
- `verification_status`：可用 `fulltext-checked`、`abstract-only`、`not-verified`、`currency-unverified`，必要时补文字说明。
- 关系中的 `support_strength`：`direct`、`partial`、`indirect`、`unsupported`、`conflicting`。这与核验状态、研究设计和方法学质量分别记录。
- 当前指南或批准信息建议记录 `version`、`checked_on` 与具体章节。

## 材料目录盘点

脚本需要 Python 3.11 或更高版本，仅使用标准库。命令从 skill 根目录运行，路径换成用户指定的位置：

```bash
python scripts/make_source_manifest.py /path/to/materials --output outputs/source_manifest.json
python scripts/make_source_manifest.py /path/to/materials --output outputs/source_manifest.json --csv outputs/source_manifest.csv --exclude /path/to/materials/outputs
```

默认仅记录相对路径、类型、大小与 SHA-256，不记录绝对源目录，不读取正文预览。跳过版本控制目录、依赖目录、`.env`、符号链接和 Windows 重解析点。当前 JSON/CSV 输出文件自动排除；其他生成目录需要用 `--exclude` 指定。

`--preview-chars 500` 显式开启文本预览，尝试 UTF-8、带 BOM 的 UTF-16 和 GB18030。不生成 DOCX/PDF/OCR 预览；这些文件仍需要用当前环境的文档工具实际打开。文件名本身也可能包含个人信息，清单不是自动脱敏产物，不能未经检查上传或发布。

`--max-files 100` 可限制盘点量。退出码 0 表示完成，1 表示截断或部分读取失败，2 表示参数/输入输出错误。查看 JSON 中的 `errors` 和 `truncated`，不能把部分清单当作全部材料。

## 按需下载论文

先核对论文身份、下载地址和可访问权限。对要下载的来源添加：

- `pdf_url`：已确认的 HTTP/HTTPS PDF 链接；不要将账号、密码或敏感令牌放入共享 JSON。
- `access`：`open-access` 或 `user-authorized`。这是操作者对访问依据的声明，脚本不负责证明版权或授权。

```bash
python scripts/download_papers.py outputs/final_references.json --output-dir outputs/papers
```

脚本按正文编号排序，未编号来源随后保留原顺序。只下载显式 `pdf_url`，不自动查询 OpenAlex、推测 PMC 地址或跟随 DOI 查找全文。需要检索时由当前环境的检索工具查到真实地址后填写；失败则保留来源落地页供人工访问。不支持登录会话、付费绕过或自动获取授权。

下载默认上限 25 MiB，可用 `--max-mb` 调整。验证 PDF 文件头，限制下载大小，不覆盖已有 PDF；先写临时文件，再通过同目录硬链接发布成品。常见 NTFS、ext4、APFS 支持此操作；不支持硬链接的文件系统会报告失败，需更换输出位置。格式初检不能证明 PDF 完整、论文身份匹配或医学结论成立。

每次运行更新 `download_report.json`；需要保留历次报告时使用不同输出目录。报告不写下载链接及其查询参数。退出码：

| 退出码 | 含义 |
| --- | --- |
| 0 | 所有待下载项均保存；不需要下载的非论文来源允许跳过 |
| 1 | 有未完成项：缺少地址/访问依据、下载失败或已有文件尚未核验 |
| 2 | 来源结构、参数或输入输出错误 |

报告状态包括 `downloaded`、`no_pdf_url`、`access_unconfirmed`、`exists_unverified`、`failed`、`not_requested`。退出码 0 也可能表示没有适用的下载项，应同时查看 `downloaded_count`。缺少链接的 `paper`/`guideline` 记为未完成；其他类型未提供链接时记为不请求下载。

脚本检查来源的基本结构和编号一致性，不校验 `claims` 的医学含义、正文首次引用顺序、重复论文身份或全文支持程度，这些仍由写作与审校流程完成。
