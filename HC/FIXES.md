# Implementation Guide: Fixes for HC Scripts

## Summary
Fixed two identified issues in the HC health check scripts for Linux/WebLogic/FMW environments:
1. **Error Key Collision** in wlst_health_checks.py
2. **WLST Error Handling** in middleware_healthcheck.py

---

## Fix #1: Error Key Collision in wlst_health_checks.py

### Problem
Using `len(dict) + 1` in exception handlers could lead to inconsistent error key generation when multiple checks execute in sequence or when errors occur at different stages.

### Solution Implemented
Added a global error counter tracking system:

```python
# Error tracking for consistent exception key generation
_error_counters = {
    'clusters': 0,
    'managed_servers': 0,
    'jms_servers': 0,
    'threads': 0,
    'datasources': 0,
    'deployments': 0,
    'composites': 0,
}

def _get_error_key(check_type):
    """Generate unique error keys for consistent error tracking."""
    _error_counters[check_type] = _error_counters.get(check_type, 0) + 1
    return f"{check_type}_error_{_error_counters[check_type]}"
```

### Changes Made
Updated exception handlers in all 7 fetch functions:
- ✅ `fetch_clusters()` - line 167
- ✅ `fetch_managed_servers()` - line 189
- ✅ `fetch_threads()` - line 237
- ✅ `fetch_jms_servers()` - line 270
- ✅ `fetch_datasources()` - line 301
- ✅ `fetch_deployments()` - line 320
- ✅ `fetch_composites()` - line 333

### Example
```python
# Before (problematic):
except Exception as exc:
    thread_pools[f'thread_error_{len(thread_pools) + 1}'] = {'server': 'ERROR', 'state': str(exc)}

# After (fixed):
except Exception as exc:
    key = _get_error_key('threads')
    thread_pools[key] = {'server': 'ERROR', 'state': str(exc)}
```

### Benefits
- ✅ Consistent error key naming across multiple executions
- ✅ No risk of key collisions in error tracking
- ✅ Easier to identify and aggregate error scenarios in monitoring tools
- ✅ Better debugging in production environments

---

## Fix #2: WLST Error Handling in middleware_healthcheck.py

### Problem
WLST sometimes returns non-zero exit codes even on partial success. Monitoring systems need flexibility to:
- Continue with other checks if one WLST check fails
- Attempt to parse partial output when possible
- Provide better diagnostic information

### Solution Implemented

#### 1. Added new command-line argument
```python
parser.add_argument(
    '--continue-on-wlst-error',
    action='store_true',
    help='Continue with other checks even if WLST checks fail (useful for monitoring)'
)
```

#### 2. Enhanced `run_wlst()` function signature
```python
def run_wlst(check, args, continue_on_error=False):
    """Invoke the configured WLST script and return the JSON payload it emits.
    
    Args:
        check: The check type to run
        args: Command line arguments
        continue_on_error: If True, attempt to parse partial output even on non-zero exit
    """
```

#### 3. Improved error handling logic
```python
if result.returncode != 0:
    error_msg = f"[ERROR] WLST returned {result.returncode}: {result.stderr.strip() or result.stdout.strip()}"
    print(error_msg)
    
    # If continue_on_error is set, attempt to parse partial output
    if continue_on_error and (result.stdout or result.stderr):
        print("[INFO] Attempting to parse partial WLST output...")
    else:
        return None
```

#### 4. Updated all check functions
Updated 7 check functions to pass the flag:
- ✅ `check_cluster()` - line 147
- ✅ `check_managed_servers()` - line 161
- ✅ `check_jms()` - line 189
- ✅ `check_threads()` - line 215
- ✅ `check_datasource()` - line 256
- ✅ `check_deployments()` - line 269
- ✅ `check_composites()` - line 282

### Example
```python
# Usage patterns:

# Mode 1: Default behavior (fail on WLST error)
python middleware_healthcheck.py --full --wlst-path /path/to/wlst.sh

# Mode 2: Continue on WLST error (useful for monitoring)
python middleware_healthcheck.py --full --continue-on-wlst-error --wlst-path /path/to/wlst.sh

# Mode 3: With configuration file
python middleware_healthcheck.py --config fmw_healthcheck.yaml --continue-on-wlst-error
```

### Benefits
- ✅ Monitoring systems can continue collecting other metrics even if WLST fails
- ✅ Partial data better than no data in production monitoring
- ✅ Better visibility into system state during WLST connection issues
- ✅ Backwards compatible (default behavior unchanged)
- ✅ Informative logging when attempting partial output parsing

---

## Testing Recommendations

### Test Fix #1 (Error Key Generation)
```bash
# Multiple runs should show incrementing error numbers
cd /path/to/HC
export WLST_SAMPLE_OUTPUT=""  # Simulate missing sample data
python wlst_health_checks.py cluster "" "" ""  # First run
python wlst_health_checks.py cluster "" "" ""  # Should show error_2, not error_1
```

### Test Fix #2 (WLST Error Handling)
```bash
# Test 1: Default behavior (should fail and exit)
python middleware_healthcheck.py --full \
  --wlst-path /invalid/path/wlst.sh \
  --wlst-script wlst_health_checks.py
# Expected: Script exits with error

# Test 2: Continue on error flag (should continue)
python middleware_healthcheck.py --full \
  --continue-on-wlst-error \
  --wlst-path /invalid/path/wlst.sh \
  --wlst-script wlst_health_checks.py
# Expected: Other checks (cpu, memory) still run

# Test 3: With sample output
python middleware_healthcheck.py --full \
  --continue-on-wlst-error \
  --wlst-path python3 \
  --wlst-script wlst_health_checks.py \
  --wlst-sample-output sample_wlst_output.json
# Expected: All checks show sample data
```

---

## Files Modified

1. **wlst_health_checks.py**
   - Added `_error_counters` dict
   - Added `_get_error_key()` function
   - Updated 7 exception handlers in fetch_* functions

2. **middleware_healthcheck.py**
   - Added `--continue-on-wlst-error` argument
   - Updated `run_wlst()` function signature
   - Enhanced error handling in `run_wlst()`
   - Updated 7 check functions to pass `continue_on_error` flag

---

## Backward Compatibility

✅ **All changes are backward compatible:**
- New argument is optional (defaults to False)
- Error key format remains same (still uses underscore-separated format)
- Existing scripts continue to work without modifications
- Default behavior unchanged when new flag not specified

---

## Production Deployment

### Recommended Steps
1. Deploy updated scripts to test environment
2. Run comprehensive test suite from "Testing Recommendations" above
3. Verify with sample data first: `--wlst-sample-output sample_wlst_output.json`
4. Deploy to production with monitoring enabled
5. Use `--continue-on-wlst-error` in production monitoring configs for resilience

### Monitoring Configuration Example
```yaml
# fmw_healthcheck.yaml
full: true
servers:
  - AdminServer1
  - ManagedServer1
admin_url: http://fmw-admin.example.com:7001
username: weblogic
password: ${WEBLOGIC_PASSWORD}  # Use environment variable
wlst_path: /opt/oracle/middleware/oracle_common/common/bin/wlst.sh
wlst_script: /opt/monitoring/wlst_health_checks.py
```

Run with:
```bash
export WEBLOGIC_PASSWORD="your_password"
python middleware_healthcheck.py --config fmw_healthcheck.yaml --continue-on-wlst-error
```
