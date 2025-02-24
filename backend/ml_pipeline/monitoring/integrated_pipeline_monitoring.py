from backend.ml_pipeline.HealthcareLLMChat.model.chat_pipeline import ChatPipeline
from backend.ml_pipeline.utils.mlflow_experiment_tracker import MLflowExperimentTracker
from backend.ml_pipeline.RAG.main import main as rag_main
from backend.config.config import Config
import time
from backend.ml_pipeline.HealthcareLLMChat.utils.prompt_templates import SYSTEM_PROMPT, INITIAL_PROMPT, FOLLOW_UP_PROMPT
from backend.ml_pipeline.HealthcareLLMChat.model.symptom_analyzer import SymptomAnalyzer
from backend.ml_pipeline.RAG.generator import Generator
from backend.ml_pipeline.utils.mlflow_save_content import save_content_as_artifact
from backend.ml_pipeline.monitoring.age_drift_monitor import AgeDriftMonitor
from backend.ml_pipeline.monitoring.retrieval_monitor import RetrievalMonitor
from datetime import date
import psycopg2

class IntegratedPipeline:
    def __init__(self, api_key: str = None):
        self.chat_pipeline = ChatPipeline(api_key)
        self.mlflow_tracker = MLflowExperimentTracker()
        self.age_monitor = AgeDriftMonitor()
        self.retrieval_monitor = RetrievalMonitor(
            score_threshold=Config.RETRIEVAL_SCORE_THRESHOLD
        )

    def _get_summary_prompt(self):
        analyzer = SymptomAnalyzer(api_key="")
        return analyzer.summary_prompt
    
    def _get_doctor_report_prompt(self):
        return Generator.DOCTOR_REPORT_PROMPT
        
    def get_recent_patient_ages(self, limit=100):
        """Fetch recent patient ages from database"""
        try:
            conn = psycopg2.connect(Config.DATABASE_URL)
            cur = conn.cursor()
            
            # Calculate age from date_of_birth
            cur.execute("""
                SELECT DATE_PART('year', AGE(date_of_birth))::int as age 
                FROM patients 
                ORDER BY created_at DESC 
                LIMIT %s
            """, (limit,))
            
            ages = [row[0] for row in cur.fetchall()]
            
            cur.close()
            conn.close()
            
            return ages
            
        except Exception as e:
            print(f"Error fetching patient ages: {e}")
            return []

    def run_conversation_and_analysis(self):
        # Start MLflow tracking
        self.mlflow_tracker.start_run()
        start_time = time.time()
        
        # Get recent patient ages for drift monitoring
        current_ages = self.get_recent_patient_ages()
        
        if current_ages:
            is_drift, p_value, statistic = self.age_monitor.check_drift(current_ages)
            
            metrics.update({
                'age_drift_detected': int(is_drift),
                'age_drift_p_value': p_value,
                'age_drift_statistic': statistic
            })
        
        # Start chat conversation
        print("Assistant:", self.chat_pipeline.start_conversation())
        
        while True:
            user_input = input("\nUser: ")
            
            if user_input.lower() in ['quit', 'exit', 'bye']:
                # Generate summary and analysis
                summary = self.chat_pipeline.generate_summary()
                medical_analysis_response = rag_main(summary)
                avg_score, min_score = self.retrieval_monitor.monitor_retrieval_scores(
                retrieval_scores,
                summary
            )
                medical_analysis = medical_analysis_response['content']
                retrieved_docs = medical_analysis_response.get('retrieved_documents', [])
                retrieval_scores = medical_analysis_response.get('retrieval_scores', [])
                
                # Prepare MLflow data
                metrics = {
                    "num_retrieved_samples": Config.N_RESULTS,
                    "input_cost": medical_analysis_response['usage']['input_cost'],
                    "output_cost": medical_analysis_response['usage']['output_cost'],
                    "total_cost": medical_analysis_response['usage']['total_cost'],
                    "prompt_tokens": medical_analysis_response['usage']['prompt_tokens'],
                    "completion_tokens": medical_analysis_response['usage']['completion_tokens'],
                    "total_tokens": medical_analysis_response['usage']['total_tokens'],
                }
                
                metrics.update({
                    "retrieval_score_avg": avg_score,
                    "retrieval_score_min": min_score
                })
                # Add retrieval scores to metrics
                for i, score in enumerate(retrieval_scores):
                    metrics[f"retrieval_score_{i+1}"] = score
                
                parameters = {
                    "summarization_model": Config.GENERATIVE_MODEL_NAME,
                    "embedding_model": Config.EMBEDDING_MODEL_NAME,
                    "n_results": Config.N_RESULTS,
                    "temperature": Config.GENERATIVE_MODEL_TEMPERATURE,
                    "max_tokens": Config.GENERATIVE_MODEL_MAX_TOKENS
                }
                
                artifacts = {
                    "summary_prompt": self._get_summary_prompt(),
                    "doctor_report_prompt": self._get_doctor_report_prompt(),
                    "pipeline_output": f"SYMPTOM SUMMARY:\n{summary}\n\nMEDICAL ANALYSIS:\n{medical_analysis}",
                    "prompt_templates": {"SYSTEM_PROMPT": SYSTEM_PROMPT, "INITIAL_PROMPT": INITIAL_PROMPT, "FOLLOW_UP_PROMPT": FOLLOW_UP_PROMPT}
                }
                
                # Log chat history separately
                chat_content = "\n".join([f"{msg['role']}: {msg['content']}" for msg in self.chat_pipeline.get_conversation_history()])
                save_content_as_artifact(chat_content, "chat_history.txt")

                # End MLflow run
                self.mlflow_tracker.end_run(metrics, parameters, artifacts)
                break
            
            # Continue conversation
            response = self.chat_pipeline.get_response(user_input)
            print("Assistant:", response)

if __name__ == "__main__":
    Config.validate_env_vars()
    pipeline = IntegratedPipeline(Config.OPENAI_API_KEY)
    pipeline.run_conversation_and_analysis() 