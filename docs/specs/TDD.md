# 🏗️ TDD: Resolution Layer Architecture

## 1. 🎯 Проблема: "The Ambiguity Problem"
Простой поиск по имени (`name_matching`) терпит крах в трех случаях:
1.  **Shadowing (Затенение):** Функция `save()` в классе `User` и функция `save()` в классе `Order`.
2.  **Aliasing (Алиасы):** `from module import func as f`. В коде мы видим `f()`, но в графе должна быть связь с `module.func`.
3.  **Namespace Scoping:** Понимание, что `self.method()` относится к текущему экземпляру класса, а `utils.helper()` — к импортированному модулю.

## 2. 🔑 Ключевая концепция: Fully Qualified Name (FQN)
Чтобы избежать путаницы, каждый узел в графе должен иметь **FQN**.
*   **Плохо:** `id: "save"`
*   **Хорошо:** `id: "project.auth.services.UserService.save"`

**FQN Formula:** `[Package/Module Path] . [Class Hierarchy] . [Symbol Name]`

---

## 3. 🔄 Архитектура: Three-Pass Resolution
Мы не можем разрешить связи за один проход по AST. Нам нужно три фазы.

### Phase 1: Declaration Indexing (Global Symbol Table)
**Цель:** Создать "карту мира" — реестр всех существующих сущностей.

1.  **Проход:** Обходим все файлы репозитория.
2.  **Действие:** Находим все `ClassDef`, `FunctionDef`, `VariableDef`.
3.  **Вычисление FQN:**
    *   Для файла `src/auth/manager.py` и функции `login`:
    *   `FQN = src.auth.manager.login`
4.  **Результат:** Глобальная `SymbolTable`:
    *   `Map<FQN, NodeMetadata>`
    *   *Пример:* `{"src.auth.manager.login": {type: FUNCTION, file: "..."}}`

### Phase 2: Import & Namespace Mapping (Local Context)
**Цель:** Для каждого файла построить "словарь перевода" с локальных имен на FQN.

1.  **Проход:** Обходим файлы по одному.
2.  **Действие:** Анализируем `import` и `from ... import ...` statements.
3.  **Построение Local Namespace Map (LNM):**
    *   Если в файле `src/api/handler.py` написано `from src.auth.manager import login as auth_login`.
    *   **LNM для этого файла:** `{"auth_login": "src.auth.manager.login", "manager": "src.auth.manager"}`.
4.  **Результат:** Список `FileContext` объектов.

### Phase 3: The Linking Pass (Edge Creation)
**Цель:** Соединить "Usage" (вызов) с "Declaration" (определением).

1.  **Проход:** Обходим AST каждого файла.
2.  **Действие:** Находим узлы `Call` (вызовы).
3.  **Алгоритм разрешения (Resolution Algorithm):**
    При обнаружении вызова `target_name`:
    *   **Шаг 1 (Local):** Проверить `LNM` текущего файла. Нашли `auth_login`? $\to$ `FQN = src.auth.manager.login`.
    *   **Шаг 2 (Scope):** Если не нашли в `LNM`, проверить текущий Scope (находится ли вызов внутри метода класса? Если да, ищем `self.target_name` в рамках этого класса).
    *   **Шаг 3 (Global):** Если не нашли локально, ищем в `Global Symbol Table` (для глобальных функций).
    *   **Шаг 4 (Match):** Если `FQN` найден в `SymbolTable` $\to$ **Создаем Edge**. Если не найден $\to$ **Создаем "Unresolved Call"** (важно для отладки!).

---

## 4. 🛠️ Data Structures (Pseudo-code)

```python
class SymbolTable:
    # Key: FQN, Value: Node ID in Graph
    symbols: Dict[str, str] 

class LocalNamespace:
    # Key: Local Name (e.g., 'f'), Value: FQN (e.g., 'mod.func')
    mapping: Dict[str, str]
    current_file: str

class Resolver:
    def resolve_call(self, call_node, current_file_context, current_scope):
        name = call_node.name
        
        # 1. Try Local Namespace (Imports)
        fqn = current_file_context.mapping.get(name)
        
        # 2. Try Class Scope (self.method)
        if not fqn and current_scope.is_inside_class:
            fqn = current_scope.resolve_member(name)
            
        # 3. Try Global
        if not fqn:
            fqn = global_symbol_table.lookup(name)
            
        return fqn
```

---

## 5. ⚠️ Edge Cases & Complexity (The "Real World" Part)

### A. Python Dynamic Behavior
*   **Проблема:** `getattr(obj, "method_name")()` или `func = get_func(); func()`.
*   **Решение для MVP:** Мы не пытаемся это разрешить детерминированно. Мы помечаем такие связи как `Edge(type=DYNAMIC_CALL, confidence=0.1)`. Это позволит агенту знать, что здесь есть "магия".

### B. TypeScript/Go Modules
*   В TS нужно учитывать `tsconfig.json` (paths/baseUrl).
*   В Go нужно учитывать `package` name и правила экспорта (Capitalized vs lowercase).
*   **Решение:** В `Phase 2` добавить "Language Specific Resolver", который подгружает конфиги сборки.

### C. Circular Dependencies
*   Если `A.py` импортирует `B.py`, а `B.py` импортирует `A.py`.
*   **Решение:** Архитектура Three-Pass это решает автоматически, так как `Phase 1` (Indexing) завершается **до** того, как мы начинаем связывать `Phase 3`.

---

## 6. 📈 Success Metrics for Resolution Layer
Как понять, что мы написали хороший Resolver?

1.  **Unresolved Rate:** Процент вызовов, которые не удалось привязать к FQN (должен стремиться к 0 в типичном проекте).
2.  **Collision Rate:** Количество случаев, когда два разных вызова одного и того же имени привели к одному и тому же `target_id` (должно быть 0, если это разные функции).
3.  **FQN Completeness:** Все ли узлы в графе имеют полный путь, а не просто короткое имя.

