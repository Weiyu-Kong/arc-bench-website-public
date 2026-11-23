import { useI18n } from "../../i18n/I18nContext";

export default function ModelConnectionNotice() {
  const { language } = useI18n();
  return (
    <div className="inline-alert model-connection-notice" role="note">
      <strong>{language === "zh" ? "模型调用与计费说明" : "Model access and accounting"}</strong>
      <p>{language === "zh"
        ? "Base URL → OPENAI_BASE_URL；API Key → OPENAI_API_KEY。智能体必须读取容器中的这两个环境变量调用模型。"
        : "Base URL → OPENAI_BASE_URL; API Key → OPENAI_API_KEY. Your agent must read these two container environment variables to call models."}</p>
      <p>{language === "zh"
        ? "请使用自己的 API Key。启用内置计费代理时，容器实际收到网站代理地址和临时 Key；填写的上游地址和真实 Key 由后端保存，代理仅向提交时填写的 Base URL 转发。"
        : "Use your own API key. With the built-in accounting proxy, the container receives a website proxy URL and temporary key; the backend keeps the upstream URL and real key and forwards requests only to the submitted Base URL."}</p>
      <p><strong>{language === "zh"
        ? "模型 API 只能通过上述环境变量访问，只有经代理转发到所填 Base URL 的请求才能参与计费。不要写死其他地址、Key，或用 .env 覆盖它们。"
        : "Access model APIs through these variables. Only requests forwarded by the proxy to the submitted Base URL can be accounted for. Do not hardcode another URL or key or override these values with .env."}</strong></p>
      <p>{language === "zh"
        ? "无需填写模型名：可在代码中固定 model，主模型和视觉模型均可自行指定，须由该接口和 Key 支持。费用按实际请求模型、返回的 usage 和网站定价计算；无 usage 或定价的请求会跳过费用统计。"
        : "No model name is required in the form. Set model in your code, including vision models supported by this endpoint and key. Costs use the requested model, returned usage and site prices; requests without usage or pricing are skipped."}</p>
    </div>
  );
}
