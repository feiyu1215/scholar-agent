# Data Availability Audit

专项检查：论文的 Data Availability Statement（数据可用性声明）是否完整、合规。

## 为什么重要

越来越多期刊要求强制性 Data Availability Statement：
- Nature/Science 系列：必须提供
- Elsevier 旗下期刊：2023 年起强制
- 中国科学院期刊：部分已要求
- 缺失或不当的声明可能导致 desk rejection

## 检查清单

### Level 1：存在性检查（presubmission_check 已覆盖）

- [ ] 论文是否包含 Data Availability 或 "数据可用性" 相关段落

### Level 2：内容完整性检查

| 检查项 | 要求 | 常见问题 |
|--------|------|----------|
| 数据来源声明 | 明确说明数据从哪来 | 模糊说"来自公开数据集"但不给出具体名称/链接 |
| 访问方式 | 如何获取数据 | "available upon request"已被多数期刊视为不充分 |
| 仓库信息 | 存放在哪个数据仓库 | 缺少 accession number 或 DOI |
| 受限数据说明 | 如果数据不能公开，为什么 | 仅说"confidential"但不说具体限制原因 |
| 代码可用性 | 分析代码/模型是否可获取 | 声明代码可用但没给链接 |
| 许可证 | 数据使用的 license | 缺失（尤其对新发布的数据集） |

### Level 3：FAIR 原则对齐检查

FAIR = Findable, Accessible, Interoperable, Reusable

| 原则 | 检查点 |
|------|--------|
| Findable | 数据有唯一持久标识符（DOI/accession number）？有描述性 metadata？ |
| Accessible | 有明确的访问协议？受限数据有申请流程说明？ |
| Interoperable | 使用通用格式（CSV/JSON/标准格式）？有数据字典/codebook？ |
| Reusable | 有明确 license？有足够的 provenance 信息？可重复分析？ |

## 常见声明类型及其合规性

### ✓ 合格声明示例

**公开数据：**
> The datasets generated and analyzed during this study are available in the
> Zenodo repository, https://doi.org/10.5281/zenodo.XXXXXXX.

**受限数据（可接受）：**
> The clinical data used in this study contain protected health information and
> are available from the corresponding author upon reasonable request and with
> permission of [institution]. Summary statistics are provided in Supplementary
> Table S1.

**混合声明：**
> Source code is available at https://github.com/xxx/yyy (MIT license).
> Preprocessed benchmark datasets are available at [DOI]. Raw clinical data
> are restricted due to patient privacy but can be requested from [ethics board].

### ✗ 不合格声明示例

**过于模糊：**
> Data is available upon request.
> ⚠ 问题：未说明向谁请求、什么条件下可获取、为什么不能公开。

**无声明：**
> （论文中完全没有 Data Availability 部分）
> ⚠ 问题：多数期刊已要求必须有，即使是"no new data were generated"也要声明。

**链接失效风险：**
> Data available at http://personal-homepage.com/~author/data.zip
> ⚠ 问题：个人网页链接非持久标识符。应使用 Zenodo/Figshare/Dryad 等仓库。

## 推荐数据仓库

### 通用仓库
- **Zenodo** (CERN): 免费、DOI 自动分配、接受任意格式
- **Figshare**: 免费、DOI、可视化预览
- **Dryad**: 偏生物/生态学，peer review 联动

### 领域专用仓库
- **基因/生物**: GenBank, SRA, ArrayExpress, PRIDE
- **社会科学**: ICPSR, UK Data Archive, Harvard Dataverse
- **化学**: Cambridge Crystallographic Data Centre
- **天文**: NASA archives, ESO
- **地球科学**: PANGAEA, NOAA
- **机器学习**: Hugging Face Datasets, Papers With Code

### 中国仓库
- **科学数据银行 (ScienceDB)**: 中科院主办
- **国家基因组科学数据中心**: 生物大数据
- **国家地球系统科学数据中心**: 地学数据

## 自动检测规则

以下规则可在 `presubmission_check.py` 中实现：

```python
# Level 1: Existence
data_availability_patterns = [
    r"(?i)data\s+availab",
    r"(?i)code\s+availab",
    r"(?i)数据可用",
    r"(?i)data\s+access",
    r"(?i)availability\s+statement",
]

# Level 2: Quality signals
GOOD_SIGNALS = [
    r"(?:doi\.org|zenodo|figshare|dryad|github\.com|gitlab\.com)",  # 有链接
    r"(?:accession\s+(?:number|code)|identifier)",                   # 有标识符
    r"(?:license|CC[- ]BY|MIT|Apache|GPL)",                         # 有许可证
]

BAD_SIGNALS = [
    r"available\s+upon\s+(?:reasonable\s+)?request(?!\s+and\s+with)",  # 模糊的upon request
    r"(?:personal|homepage|~\w+/)",                                    # 非持久链接
    r"not\s+(?:publicly\s+)?available(?!\s+(?:due|because))",         # 不可用但无理由
]
```

## 与 review_engine 的集成

当 presubmission_check 发现 Data Availability 声明缺失或薄弱时：
1. 标记为 WARNING（如果完全缺失则为 ERROR）
2. 在 review_paper 阶段，Methodology reviewer 深入检查其合规性
3. 生成具体建议：推荐仓库、声明模板、需要补充的信息

## 声明模板（供 guidance 模式使用）

### 模板 1：完全公开数据
> The data that support the findings of this study are openly available in
> [Repository Name] at [DOI/URL], reference number [accession].

### 模板 2：受限数据
> The data that support the findings of this study are available from
> [Source/Institution] but restrictions apply to the availability of these data,
> which were used under license for the current study, and so are not publicly
> available. Data are however available from the authors upon reasonable request
> and with permission of [Authority].

### 模板 3：无新数据
> No new data were created or analyzed in this study. Data sharing is not
> applicable to this article.

### 模板 4：代码+数据混合
> Source code for reproducing all experiments is available at [GitHub URL]
> under [License]. Training data are available at [DOI]. Pretrained model
> weights are hosted at [URL].
