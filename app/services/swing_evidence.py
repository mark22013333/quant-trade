"""只接受單一公告主體的有限明確陳述；有歧義的自由文字一律拒收。"""
import re


ISSUER = r"(?:公司代號|公司代碼|股票代號|證券代號)[：:\s]*([0-9A-Z]{2,20})(?![0-9A-Z])"
PRIMARY = re.compile(r"\A\s*(?:公告公司[：:\s]*)?" + ISSUER)
AMBIGUOUS = re.compile(r"否認|不實|引述|媒體|報導|澄清|假設|如果|可能遭|競爭對手|其他公司|同業|尚未證實|[「」『』“”\"']")


def event_fact_errors(original, *, symbol, event, exclude, require_issuer=False):
    errors = []
    if type(exclude) is not bool:
        return ["invalid_exclude_recommended"]
    issuers = set(re.findall(ISSUER, original))
    primary = PRIMARY.match(original)
    if issuers and (issuers != {symbol} or primary is None or primary.group(1) != symbol):
        errors.append("ambiguous_or_wrong_issuer")
    if require_issuer and (primary is None or issuers != {symbol}):
        errors.append("issuer_identity_not_verified")
    if AMBIGUOUS.search(original):
        errors.append("ambiguous_quoted_or_denied_fact")
    body = original[primary.end():].lstrip("，,。\n \t") if primary else original
    facts = [re.sub(r"^公司公告[：:]\s*", "", line.strip())
             for line in re.split(r"[。！？\n]", body) if line.strip()]
    clear_statements = [bool(re.fullmatch(
        r"(?:本公司|公司)(?:營運狀況正常[，,]\s*)?(?:本次事件未出現新增重大風險|本月無重大裁罰)", line
    )) for line in facts]
    veto_statements = [bool(re.match(r"(?:本公司|本集團|公司)(?:遭|受|接獲).{0,12}(?:重大裁罰|裁罰|罰鍰)", line)
                            and not re.search(r"無|未|不", line)) for line in facts]
    clear, veto = any(clear_statements), any(veto_statements)
    # 不從長篇自由文字挑選有利句子；不能完整辨識的公告就維持未知。
    if not facts or any(not known_clear and not known_veto
                        for known_clear, known_veto in zip(clear_statements, veto_statements)):
        errors.append("unsupported_announcement_statement")
    if clear and veto:
        errors.append("contradictory_event_facts")
    if exclude:
        if event != "重大裁罰" or not veto:
            errors.append("claim_not_supported")
    elif event not in ("無新增重大風險", "營運正常", "重大裁罰") or not clear:
        errors.append("unsupported_event_semantics")
    return errors
