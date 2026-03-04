#!/usr/bin/env python3
"""Minimal KFP Demo Pipeline"""
from kfp import dsl
from kfp.dsl import Dataset, Output, Input


@dsl.component(base_image="python:3.11-slim")
def generate_data(output: Output[Dataset]) -> None:
    """Generate sample CSV file (artifact to Minio)."""
    with open("/tmp/data.csv", "w") as f:
        f.write("id,value\n")
        for i in range(1, 101):
            f.write(f"{i},{i*10}\n")
    output.path = "/tmp/data.csv"
    print("Generated: data.csv (100 rows)")


@dsl.component(base_image="python:3.11-slim", packages_to_install=["pandas"])
def process_data(data: Input[Dataset]) -> float:
    """Load CSV, compute mean, return metric."""
    import pandas as pd

    df = pd.read_csv(data.path)
    mean_value = df["value"].mean()
    print(f"Mean value: {mean_value}")
    return mean_value


@dsl.pipeline(name="demo-pipeline", description="Minimal KFP demo")
def demo_pipeline() -> float:
    """Orchestrate: generate → process → return result."""
    gen_task = generate_data()
    result = process_data(data=gen_task.outputs["output"])
    return result


if __name__ == "__main__":
    from kfp.compiler import Compiler

    # Compile to YAML
    Compiler().compile(
        pipeline_func=demo_pipeline,
        package_path="demo_pipeline.yaml"
    )
    print("Pipeline compiled: demo_pipeline.yaml")
    print("\nTo submit from JupyterHub notebook:")
    print("```python")
    print("import kfp")
    print("from demo_pipeline import demo_pipeline")
    print("")
    print("client = kfp.Client(host='http://ml-pipeline-ui.kubeflow:80')")
    print("run = client.create_run_from_pipeline_func(demo_pipeline, arguments={})")
    print("print(f'Run: {run.run_id}')")
    print("```")
