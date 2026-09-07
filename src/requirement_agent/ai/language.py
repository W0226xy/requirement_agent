import json
import re
from pydantic import BaseModel
from requirement_agent.shared.enums import AnalysisType
#判断原始需求是否包含中文；如果包含中文，就要求大模型使用中文输出，并在模型返回后再次检查关键字段是否真的包含中文。
#整体流程：检查原始需求语言
#→ 生成语言提示词
#→ 大模型返回结果
#→ 提取关键自然语言字段
#→ 检查结果是否包含中文
#→ 不符合要求则抛出异常并触发模型重试

CJK_PATTERN = re.compile(r"[\u3400-\u9fff]")#中文字符正则表达式，主要用它判断是否包含中文汉字。
#re.compile()函数用于编译正则表达式模式，返回一个正则表达式对象。这里的模式 [\u3400-\u9fff] 匹配所有 CJK（中日韩）统一汉字字符，包括常用汉字和扩展汉字。
#生成语言提示词
def source_language_instruction(source_content: str) -> str:
    if contains_chinese(source_content):#检查文本是否包含中文
        return "The source language is Chinese. All natural-language output fields must be Chinese."
    return "All natural-language output fields must use the same language as the source."

#检查大模型输出的自然语言字段是否符合源语言要求
def validate_output_language(
    source_content: str,#用户提交的原始需求
    result: BaseModel,#大模型返回并经过Pydantic解析的对象
    analysis_type: AnalysisType,#当前属于需求提取还是冲突风险分析
) -> None:
    if not contains_chinese(source_content):#如果原始输入不包含中文，就直接结束校验。
        return
    payload = result.model_dump(mode="json")#把模型结果转换成字典，表示尽量转换成JSON兼容的数据，
    # 例如枚举转换成枚举值；时间转换成字符串；嵌套Pydantic对象转换成字典。

    #需求提取结果的语言检查
    if analysis_type == AnalysisType.EXTRACTION:#当前分析类型是需求提取
        natural_text = " ".join(
            str(payload.get(field, ""))#获取字典中指定字段的值，如果字段不存在则返回空字符串
            for field in ("requirement_summary", "requirement_description")#提取需求摘要和需求描述两个自然语言字段
        )
    else:#当前分析类型是冲突风险分析（AnalysisType.CONFLICT_RISK）
        natural_text = json.dumps(
            {
                "conflicts": payload.get("conflicts", []),#获取冲突列表，如果不存在则返回空列表
                "risks": payload.get("risks", []),#获取风险列表，如果不存在则返回空列表
                "clarification_questions": payload.get("clarification_questions", []),#获取澄清问题列表，如果不存在则返回空列表
            },
            ensure_ascii=False,
        )
        if natural_text in {
            '{"conflicts": [], "risks": [], "clarification_questions": []}',
            "",
        }:
            return
    if not contains_chinese(natural_text):#如果自然语言文本不包含中文，就抛出异常
        raise ValueError("natural-language output must be Chinese for Chinese source input")


def contains_chinese(value: str) -> bool:
    return CJK_PATTERN.search(value) is not None
