OUTPUT_CONTRACT = """최종 JSON에는 outcome, summary, causes, next_actions, evidence를 포함한다.
causes에는 실제 관찰된 증거로 뒷받침되는 진단 원인만 기록한다. 확인되지 않은 가능성이나 추측은 원인으로 기록하지 않는다.
확인된 원인이 없으면 causes=[]로 반환한다. 여러 원인이 확인되면 모두 기록한다.
현재 정보로 원인을 확정할 수 없으면 outcome=needs_information을 사용한다. 일부 원인이 확인되어도 다른 정보가 부족하면 needs_information을 사용할 수 있으며, 이때 확인된 원인은 causes에 유지한다.
추가 정보가 필요하면 검증할 사항이나 필요한 정보를 next_actions에 구체적인 확인 요청이나 질문으로 기록한다. 미검증 가능성은 확정된 원인처럼 표현하지 않는다.
summary는 확인된 진단과 정보의 한계를 요약하며 causes 및 outcome과 모순되지 않아야 한다.
diagnosed는 원인과 실제 성공 관찰 근거가 필요하다. 범위 밖이면 escalate와 이관 안내를 제공한다."""
