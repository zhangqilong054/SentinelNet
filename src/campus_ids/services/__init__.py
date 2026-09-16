"""services — 业务用例包。

每个 service 封装一组业务逻辑，不依赖 Web 框架（Flask/FastAPI），
可独立单测。Web 层通过依赖注入调用 services。

阶段1为空壳，阶段2接入实际业务逻辑。
"""